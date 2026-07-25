from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..db.schema import (
    media_library_analysis_runs,
    media_library_assets,
    media_library_clip_derivatives,
    media_library_fragment_index,
    media_library_tasks,
    session_files,
)
from ..koubo.koubo_storyboard.asset_search_services import (
    translate_search_text_to_english_keywords,
)
from ..media_library_features import require_media_library_feature
from ..media_library_imports.repository import MediaLibraryImportRepository
from .schemas import MediaLibrarySearchAction
from .schemas import MediaLibrarySearchRequest


class StoryBoardMediaSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_text: str = Field(default="", max_length=4000)
    orientation: Literal["any", "portrait", "landscape"] = "any"
    limit: int = Field(default=12, ge=1, le=50)


class SearchFragmentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme: Literal["dialogue", "visual_semantic", "composite"]
    run_id: str = Field(min_length=1, max_length=256)
    fragment_id: str = Field(min_length=1, max_length=256)


class EditorMediaSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_task_id: int | None = None
    sources: list[Literal["external", "media_library"]] = Field(
        default_factory=lambda: ["media_library"],
        min_length=1,
        max_length=2,
    )
    fragment_refs: list[SearchFragmentRef] = Field(default_factory=list, max_length=20)
    user_text: str = Field(default="", max_length=4000)
    orientation: Literal["any", "portrait", "landscape"] = "any"
    limit: int = Field(default=12, ge=1, le=50)


class EditorSearchActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_kind: Literal["preview", "open_editor"]
    source: Literal["external", "media_library"]
    candidate_id: str = Field(min_length=1, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _storyboard_dialogues(value: dict[str, Any]) -> list[dict[str, Any]]:
    dialogues: list[dict[str, Any]] = []
    for shot in value.get("shots") if isinstance(value.get("shots"), list) else []:
        if not isinstance(shot, dict):
            continue
        for scene in shot.get("scenes") if isinstance(shot.get("scenes"), list) else []:
            if not isinstance(scene, dict):
                continue
            for field in ("dialogues", "dialogue_items"):
                candidates = scene.get(field)
                if isinstance(candidates, list):
                    dialogues.extend(item for item in candidates if isinstance(item, dict))
    return dialogues


def _target_dialogue(ctx: Any, task_id: int, dialogue_asset_key: str) -> dict[str, Any]:
    target = MediaLibraryImportRepository(ctx.engine).get_target_task(task_id)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "storyboard_target_not_found",
                "user_message": "StoryBoard Task 不存在。",
            },
        )
    if str(target.get("status") or "").lower() in {
        "archived",
        "deleting",
        "deleted",
        "cancelled",
    }:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "storyboard_target_invalid",
                "user_message": "StoryBoard Task 当前不可用于检索。",
            },
        )
    workspace = Path(str(target.get("workspace_dir") or "")).resolve()
    if not workspace.is_dir():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "storyboard_target_invalid",
                "user_message": "StoryBoard workspace 不存在。",
            },
        )
    services = getattr(ctx, "koubo_storyboard_services", None)
    if services is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "storyboard_service_unavailable",
                "user_message": "StoryBoard 服务尚未就绪。",
            },
        )
    try:
        task = services.task_or_404(task_id)
        authoritative_workspace = Path(
            services.workspace_for(task)
        ).resolve()
        if authoritative_workspace != workspace:
            raise ValueError("storyboard_target_workspace_mismatch")
        storyboard, _meta = services.load_plan(task, sc=services)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "storyboard_result_missing",
                "user_message": "StoryBoard 尚未生成可检索的 Dialogue。",
            },
        ) from exc
    matches = [
        dialogue
        for dialogue in _storyboard_dialogues(storyboard)
        if str(dialogue.get("dialogue_asset_key") or "")
        == dialogue_asset_key
    ]
    if len(matches) != 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "storyboard_dialogue_stale",
                "user_message": "当前 Dialogue 已变化，请刷新 StoryBoard 后重试。",
            },
        )
    dialogue = matches[0]
    text = str(dialogue.get("text") or "").strip()
    if not text:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "storyboard_dialogue_empty",
                "user_message": "当前 Dialogue 没有可用于检索的文本。",
            },
        )
    return dialogue


def _combined_query(dialogue: dict[str, Any], user_text: str) -> str:
    values = [
        str(dialogue.get("text") or "").strip(),
        str(user_text or "").strip(),
    ]
    return " ".join(value for value in values if value)


def _editor_fragment_query(ctx: Any, asset_id: str, refs: list[SearchFragmentRef], user_text: str) -> str:
    texts: list[str] = []
    with ctx.engine.connect() as conn:
        for ref in refs:
            row = (
                conn.execute(
                    select(media_library_fragment_index)
                    .join(
                        media_library_analysis_runs,
                        media_library_analysis_runs.c.analysis_run_id == media_library_fragment_index.c.analysis_run_id,
                    )
                    .where(
                        media_library_fragment_index.c.asset_id == asset_id,
                        media_library_fragment_index.c.analysis_scheme == ref.scheme,
                        media_library_fragment_index.c.analysis_run_id == ref.run_id,
                        media_library_fragment_index.c.fragment_id == ref.fragment_id,
                        media_library_fragment_index.c.is_active.is_(True),
                        media_library_analysis_runs.c.status == "ready",
                        media_library_analysis_runs.c.is_current.is_(True),
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "analysis_result_stale",
                        "user_message": "用于检索的分析片段已失效，请刷新剪辑页。",
                    },
                )
            value = str(row.get("dialogue_text") or row.get("summary") or row.get("title") or "").strip()
            if value:
                texts.append(value)
    if str(user_text or "").strip():
        texts.append(str(user_text).strip())
    query = " ".join(texts)
    if not query:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "search_query_required",
                "user_message": "请选择分析片段或输入检索内容。",
            },
        )
    return query


def _external_search_payload(
    *,
    query: str,
    orientation: str,
    limit: int,
) -> dict[str, Any]:
    translated_query, translated_ok = (
        translate_search_text_to_english_keywords(query)
    )
    effective_query = translated_query if translated_ok else query
    language = (
        "en"
        if translated_ok
        else ("zh" if any("\u3400" <= char <= "\u9fff" for char in query) else "und")
    )
    aspect = {
        "portrait": "9:16",
        "landscape": "16:9",
    }.get(orientation, "auto")
    sources = ["pexels", "pixabay", "wikimedia", "unsplash"]
    return {
        "user_text": query,
        "media_types": ["video"],
        "sources": sources,
        "aspect": aspect,
        "limit_per_source": limit,
        "safe_search": True,
        "plan": {
            "summary": "Editor external video candidates",
            "media_types": ["video"],
            "sources": sources,
            "aspect": aspect,
            "queries": [
                {
                    "query": effective_query,
                    "language": language,
                    "media_type": "video",
                    "priority": 1,
                }
            ],
            "license_policy": "prefer_open_or_free_commercial",
            "risk_notes": (
                []
                if translated_ok
                else ["external_query_translation_unavailable"]
            ),
            "degraded": not translated_ok,
        },
    }


def _external_candidate(value: dict[str, Any], *, provider_search_id: str) -> dict[str, Any]:
    duration_seconds = value.get("duration_seconds")
    try:
        duration_ms = max(0, round(float(duration_seconds) * 1000)) if duration_seconds is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    creator = dict(value.get("creator")) if isinstance(value.get("creator"), dict) else {}
    license_payload = dict(value.get("license")) if isinstance(value.get("license"), dict) else {}
    return {
        "source": "external",
        "candidate_id": str(value.get("candidate_id") or ""),
        "provider": str(value.get("provider") or ""),
        "provider_asset_id": str(value.get("provider_asset_id") or ""),
        "provider_search_id": provider_search_id,
        "asset_id": None,
        "source_version": None,
        "display_name": str(value.get("title") or value.get("description") or "外部视频候选"),
        "description": str(value.get("description") or ""),
        "preview_url": value.get("preview_url"),
        "thumbnail_url": value.get("thumbnail_url"),
        "source_url": value.get("source_url"),
        "duration_ms": duration_ms,
        "width": int(value.get("width") or 0) or None,
        "height": int(value.get("height") or 0) or None,
        "orientation": str(value.get("orientation") or "unknown"),
        "creator": {
            "name": str(creator.get("name") or ""),
            "url": str(creator.get("url") or ""),
        },
        "license": {
            "name": str(license_payload.get("name") or ""),
            "url": str(license_payload.get("url") or ""),
            "requires_attribution": bool(license_payload.get("requires_attribution")),
            "attribution_text": str(license_payload.get("attribution_text") or ""),
            "license_status": str(license_payload.get("license_status") or "unknown"),
        },
        "score": float(value.get("score") or 0),
        "score_reasons": [str(item) for item in (value.get("score_reasons") or []) if str(item)][:8],
        "matched_fragments": [],
        # External candidates remain whole-video imports. They never receive
        # an asset_id or an editor action.
        "allowed_actions": ["preview", "import_whole"],
        "import_supported": bool(value.get("import_supported", True)),
        "import_unsupported_reason": str(value.get("import_unsupported_reason") or ""),
    }


async def _external_editor_search(
    ctx: Any,
    *,
    target_task_id: int,
    query: str,
    orientation: str,
    limit: int,
) -> dict[str, Any]:
    deps = getattr(ctx, "koubo_storyboard_services", None)
    if deps is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "external_search_service_unavailable",
                "user_message": "外部素材服务尚未就绪。",
            },
        )
    task = deps.task_or_404(target_task_id)
    completed: dict[str, Any] | None = None
    failed: dict[str, Any] | None = None
    search_payload = _external_search_payload(
        query=query,
        orientation=orientation,
        limit=limit,
    )
    # Let the existing StoryBoard provider service perform its configured
    # planner/translation flow. The plan returned by the editor plan endpoint
    # is only a preview and must not bypass that authoritative path.
    search_payload.pop("plan", None)
    async for event in deps.stream_asset_search_events(
        task,
        search_payload,
        sc=deps,
    ):
        if event.get("type") == "completed":
            completed = event
        elif event.get("type") == "failed":
            failed = event
    if completed is None:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "external_search_failed",
                "user_message": str((failed or {}).get("detail") or "外部素材检索失败，请稍后重试。"),
            },
        )
    provider_search_id = str(completed.get("search_id") or "")
    if not provider_search_id:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "external_search_run_invalid",
                "user_message": "外部素材检索未返回可回放的运行标识。",
            },
        )
    return {
        "search_id": provider_search_id,
        "items": [
            _external_candidate(item, provider_search_id=provider_search_id)
            for item in (completed.get("items") or [])
            if isinstance(item, dict) and str(item.get("media_type") or "") == "video" and str(item.get("candidate_id") or "")
        ],
        "provider_stats": completed.get("provider_stats") or {},
        "ranking": completed.get("ranking") or {},
    }


def _run_for_context(ctx: Any, search_id: str, expected: dict[str, Any]) -> dict[str, Any]:
    service = ctx.media_library_search_service
    run = service.get_run(search_id)
    for field, value in expected.items():
        if value is not None and run.get(field) != value:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "search_run_not_found",
                    "user_message": "检索运行不属于当前页面上下文。",
                },
            )
    return run


def _external_replay_items(
    ctx: Any,
    *,
    run: dict[str, Any],
    snapshots: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    source_runs = dict(run.get("source_runs_json") or {}) if isinstance(run.get("source_runs_json"), dict) else {}
    provider_search_id = str(source_runs.get("external") or "")
    if not provider_search_id:
        return [], None
    target_task_id = int(run.get("target_task_id") or 0)
    deps = getattr(ctx, "koubo_storyboard_services", None)
    if deps is None or target_task_id <= 0:
        return [], {"code": "external_search_replay_unavailable"}
    try:
        task = deps.task_or_404(target_task_id)
        provider_run = deps.load_asset_search_run(task, provider_search_id, sc=deps)
        if (
            str(provider_run.get("search_id") or "") != provider_search_id
            or int(provider_run.get("task_id") or 0) != target_task_id
            or (provider_run.get("session_id") is not None and int(provider_run.get("session_id") or 0) != int(task.get("session_id") or 0))
        ):
            raise ValueError("external_search_run_context_mismatch")
    except Exception:
        return [], {"code": "external_search_replay_unavailable"}

    candidates = {
        str(item.get("candidate_id") or ""): item
        for item in provider_run.get("candidates") or []
        if isinstance(item, dict) and str(item.get("media_type") or "") == "video"
    }
    items: list[dict[str, Any]] = []
    for snapshot in sorted(snapshots, key=lambda item: int(item.get("rank") or 0)):
        candidate_id = str(snapshot.get("candidate_id") or "")
        candidate = candidates.get(candidate_id)
        if candidate is None:
            continue
        items.append(_external_candidate(candidate, provider_search_id=provider_search_id))
    return items, None


def _replay_response(ctx: Any, search_id: str, expected: dict[str, Any]) -> dict[str, Any]:
    run = _run_for_context(ctx, search_id, expected)
    snapshots = []
    for item in run.get("top_candidates_json") or []:
        if not isinstance(item, dict) or str(item.get("source") or "") != "media_library":
            continue
        candidate_kind = str(item.get("candidate_kind") or "")
        if not candidate_kind:
            candidate_kind = "original_video"
        if candidate_kind not in {"original_video", "derived_clip"}:
            continue
        snapshots.append({**item, "candidate_kind": candidate_kind})
    items: list[dict[str, Any]] = []
    with ctx.engine.connect() as conn:
        for snapshot in sorted(snapshots, key=lambda item: int(item.get("rank") or 0)):
            candidate_kind = str(snapshot["candidate_kind"])
            if candidate_kind == "derived_clip":
                if not ctx.media_library_search_service.repository._clip_search_enabled():
                    continue
                clip_id = str(snapshot.get("candidate_id") or "")
                source_clip_id = str(snapshot.get("source_clip_id") or "")
                source_asset_id = str(
                    snapshot.get("source_asset_id") or ""
                )
                if not clip_id or source_clip_id != clip_id or not source_asset_id:
                    continue
                clip = conn.execute(
                    select(
                        media_library_clip_derivatives,
                        media_library_assets.c.width,
                        media_library_assets.c.height,
                    )
                    .select_from(
                        media_library_clip_derivatives.join(
                            media_library_assets,
                            media_library_assets.c.asset_id
                            == media_library_clip_derivatives.c.source_asset_id,
                        ).join(
                            session_files,
                            (
                                session_files.c.session_id
                                == media_library_clip_derivatives.c.source_session_id
                            )
                            & (
                                session_files.c.path
                                == media_library_clip_derivatives.c.output_path
                            ),
                        )
                    )
                    .where(
                        media_library_clip_derivatives.c.clip_id == clip_id,
                        media_library_clip_derivatives.c.source_asset_id
                        == source_asset_id,
                        media_library_clip_derivatives.c.search_eligible.is_(
                            True
                        ),
                        media_library_assets.c.upload_status == "ready",
                        media_library_assets.c.archived.is_(False),
                        media_library_clip_derivatives.c.source_version
                        == media_library_assets.c.content_sha256,
                        session_files.c.kind == "video",
                        session_files.c.stale == 0,
                        session_files.c.downloadable != 0,
                    )
                ).mappings().first()
                if clip is None:
                    continue
                snapshot_version = str(
                    snapshot.get("source_version") or ""
                )
                snapshot_hash = str(
                    snapshot.get("content_sha256") or ""
                )
                if (
                    snapshot_version != str(clip["source_version"])
                    or snapshot_hash != str(clip["content_sha256"])
                ):
                    continue
                path = str(clip.get("output_path") or "")
                encoded_path = "/".join(
                    quote(part, safe="") for part in path.split("/")
                )
                session_id = int(clip.get("source_session_id") or 0)
                duration_ms = int(clip.get("duration_ms") or 0)
                width, height = clip.get("width"), clip.get("height")
                orientation = (
                    "any"
                    if width is None or height is None
                    else "portrait"
                    if int(height) > int(width)
                    else "landscape"
                )
                items.append(
                    {
                        "source": "media_library",
                        "candidate_kind": "derived_clip",
                        "candidate_id": clip_id,
                        "asset_id": None,
                        "source_asset_id": source_asset_id,
                        "source_clip_id": clip_id,
                        "source_version": str(clip["source_version"]),
                        "content_sha256": str(clip["content_sha256"]),
                        "display_name": str(clip["display_name"]),
                        "tags": list(clip.get("tags_json") or []),
                        "preview_url": (
                            f"/api/session-tasks/{session_id}/raw/{encoded_path}"
                        ),
                        "thumbnail_url": None,
                        "duration_ms": duration_ms,
                        "candidate_start_ms": 0,
                        "candidate_end_ms": duration_ms,
                        "source_start_ms": int(clip["source_start_ms"]),
                        "source_end_ms": int(clip["source_end_ms"]),
                        "time_basis": "candidate",
                        "orientation": orientation,
                        "score": float(snapshot.get("score") or 0),
                        "score_reasons": ["已知检索运行的当前有效片段"],
                        "matched_fragments": [],
                        "license": None,
                        "allowed_actions": ["preview", "import_clip"],
                    }
                )
                continue
            asset_id = str(snapshot.get("source_asset_id") or "")
            fragment_ids = [str(value) for value in snapshot.get("matched_fragment_ids") or [] if str(value)]
            if not asset_id or not fragment_ids:
                continue
            rows = [
                dict(row)
                for row in conn.execute(
                    select(
                        media_library_fragment_index,
                        media_library_assets.c.display_name,
                        media_library_assets.c.thumbnail_url,
                        media_library_assets.c.preview_url,
                        media_library_assets.c.duration_ms,
                        media_library_assets.c.width,
                        media_library_assets.c.height,
                        media_library_tasks.c.dialogue_status.label(
                            "task_dialogue_status"
                        ),
                        media_library_tasks.c.visual_semantic_status.label(
                            "task_visual_semantic_status"
                        ),
                        media_library_analysis_runs.c.schema_version.label(
                            "run_schema_version"
                        ),
                    )
                    .select_from(
                        media_library_fragment_index.join(
                            media_library_assets,
                            media_library_assets.c.asset_id == media_library_fragment_index.c.asset_id,
                        )
                        .join(
                            media_library_tasks,
                            media_library_tasks.c.asset_id == media_library_fragment_index.c.asset_id,
                        )
                        .join(
                            media_library_analysis_runs,
                            media_library_analysis_runs.c.analysis_run_id == media_library_fragment_index.c.analysis_run_id,
                        )
                    )
                    .where(
                        media_library_fragment_index.c.asset_id == asset_id,
                        media_library_fragment_index.c.fragment_id.in_(fragment_ids),
                        media_library_fragment_index.c.is_active.is_(True),
                        media_library_assets.c.upload_status == "ready",
                        media_library_assets.c.archived.is_(False),
                        media_library_analysis_runs.c.status == "ready",
                        media_library_analysis_runs.c.is_current.is_(True),
                        media_library_fragment_index.c.source_version == media_library_assets.c.content_sha256,
                    )
                )
                .mappings()
                .fetchall()
            ]
            visual_enabled = (
                ctx.media_library_search_service.repository._visual_search_enabled()
            )
            rows = [
                row
                for row in rows
                if ctx.media_library_search_service.repository._row_scheme_eligible(
                    row, visual_enabled=visual_enabled
                )
            ]
            by_id = {str(row["fragment_id"]): row for row in rows}
            ordered = [by_id[value] for value in fragment_ids if value in by_id]
            if not ordered:
                continue
            first = ordered[0]
            snapshot_version = str(snapshot.get("source_version") or "")
            if snapshot_version and snapshot_version != str(first["source_version"]):
                continue
            snapshot_content_hash = str(
                snapshot.get("content_sha256") or snapshot_version
            )
            if snapshot_content_hash and snapshot_content_hash != str(
                first["source_version"]
            ):
                continue
            width, height = first.get("width"), first.get("height")
            orientation = "any" if width is None or height is None else "portrait" if int(height) > int(width) else "landscape"
            items.append(
                {
                    "source": "media_library",
                    "candidate_kind": "original_video",
                    "candidate_id": asset_id,
                    "asset_id": asset_id,
                    "source_asset_id": asset_id,
                    "source_clip_id": None,
                    "source_version": str(first["source_version"]),
                    "content_sha256": str(first["source_version"]),
                    "display_name": str(first["display_name"]),
                    "preview_url": first.get("preview_url"),
                    "thumbnail_url": first.get("thumbnail_url"),
                    "duration_ms": first.get("duration_ms"),
                    "orientation": orientation,
                    "score": float(snapshot.get("score") or 0),
                    "score_reasons": ["已知检索运行的当前有效命中"],
                    "matched_fragments": [
                        {
                            "scheme": str(row["analysis_scheme"]),
                            "analysis_scheme": str(row["analysis_scheme"]),
                            "run_id": str(row["analysis_run_id"]),
                            "fragment_id": str(row["fragment_id"]),
                            "start_ms": int(row["start_ms"]),
                            "end_ms": int(row["end_ms"]),
                            "dialogue_text": row.get("dialogue_text"),
                            "summary": row.get("summary"),
                            "keyframe_ref": row.get("keyframe_ref_json"),
                            "score_reasons": [],
                        }
                        for row in ordered
                    ],
                    "license": None,
                    "allowed_actions": [
                        "preview",
                        "open_editor",
                        "import_original",
                    ],
                }
            )
    source_runs = dict(run.get("source_runs_json") or {}) if isinstance(run.get("source_runs_json"), dict) else {}
    persisted_errors = dict(source_runs.get("source_errors") or {}) if isinstance(source_runs.get("source_errors"), dict) else {}
    replay_errors: dict[str, Any] = dict(persisted_errors)
    if str(run.get("entry_point") or "") == "editor":
        external_snapshots = [item for item in run.get("top_candidates_json") or [] if isinstance(item, dict) and str(item.get("source") or "") == "external"]
        external_items, external_error = _external_replay_items(ctx, run=run, snapshots=external_snapshots)
        items.extend(external_items)
        if external_error is not None:
            replay_errors["external"] = external_error
        rank_by_candidate = {
            (
                str(item.get("source") or ""),
                str(item.get("candidate_id") or ""),
            ): int(item.get("rank") or 0)
            for item in run.get("top_candidates_json") or []
            if isinstance(item, dict)
        }
        items.sort(
            key=lambda item: rank_by_candidate.get(
                (
                    str(item.get("source") or ""),
                    str(item.get("candidate_id") or ""),
                ),
                1_000_000,
            )
        )
    response = {
        "search_id": search_id,
        "retrieval_version": str(run.get("retrieval_version") or ""),
        "planner_version": str(run.get("planner_version") or ""),
        "planner_degraded": bool(run.get("planner_degraded")),
        "result_count": len(items),
        "total_count": int(run.get("result_count") or len(items)),
        "limit": max(1, len(items)),
        "offset": 0,
        "items": items,
        "status": str(run.get("status") or ""),
    }
    if str(run.get("entry_point") or "") == "editor":
        response.update(
            {
                "search_runs": {
                    "media_library": source_runs.get("media_library"),
                    "external": source_runs.get("external"),
                },
                "requested_sources": list(run.get("requested_sources_json") or []),
                "source_errors": replay_errors,
            }
        )
    return response


def build_media_library_search_router(ctx: Any) -> APIRouter:
    router = APIRouter(tags=["media-library-search"])

    @router.post("/api/koubo-storyboard/tasks/{task_id}/dialogues/" "{dialogue_asset_key}/media-library-search/plan")
    async def storyboard_search_plan(
        task_id: int,
        dialogue_asset_key: str,
        payload: StoryBoardMediaSearchInput,
    ) -> dict[str, Any]:
        require_media_library_feature("library_search")
        dialogue = _target_dialogue(ctx, task_id, dialogue_asset_key)
        outcome = await ctx.media_library_search_service.plan(
            MediaLibrarySearchRequest(
                query=_combined_query(dialogue, payload.user_text),
                dialogue_query=str(dialogue.get("text") or "").strip(),
                user_query=payload.user_text.strip(),
                entry_point="storyboard",
                query_source="dialogue",
                target_task_id=task_id,
                dialogue_asset_key=dialogue_asset_key,
                orientation=payload.orientation,
                limit=payload.limit,
            )
        )
        return {
            "plan": outcome.plan.model_dump(mode="json"),
            "planner_degraded": outcome.degraded,
            "planner_latency_ms": outcome.latency_ms,
            "error_code": outcome.error_code,
        }

    @router.post("/api/koubo-storyboard/tasks/{task_id}/dialogues/" "{dialogue_asset_key}/media-library-search/runs")
    async def storyboard_search_run(
        task_id: int,
        dialogue_asset_key: str,
        payload: StoryBoardMediaSearchInput,
    ) -> dict[str, Any]:
        require_media_library_feature("library_search")
        dialogue = _target_dialogue(ctx, task_id, dialogue_asset_key)
        result = await ctx.media_library_search_service.search(
            MediaLibrarySearchRequest(
                query=_combined_query(dialogue, payload.user_text),
                dialogue_query=str(dialogue.get("text") or "").strip(),
                user_query=payload.user_text.strip(),
                entry_point="storyboard",
                query_source="dialogue",
                target_task_id=task_id,
                dialogue_asset_key=dialogue_asset_key,
                orientation=payload.orientation,
                limit=payload.limit,
            )
        )
        return result.model_dump(mode="json")

    @router.get("/api/koubo-storyboard/tasks/{task_id}/media-library-search/runs/" "{search_id}")
    async def storyboard_search_replay(task_id: int, search_id: str) -> dict[str, Any]:
        return _replay_response(
            ctx,
            search_id,
            {"entry_point": "storyboard", "target_task_id": task_id},
        )

    @router.post("/api/media-library/{asset_id}/search/plan")
    async def editor_search_plan(asset_id: str, payload: EditorMediaSearchInput) -> dict[str, Any]:
        require_media_library_feature("editor")
        require_media_library_feature("library_search")
        if "external" in payload.sources and payload.target_task_id is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "search_target_task_required",
                    "user_message": "启用外部素材来源前必须选择目标 StoryBoard Task。",
                },
            )
        fragment_query = (
            _editor_fragment_query(ctx, asset_id, payload.fragment_refs, "")
            if payload.fragment_refs
            else ""
        )
        user_query = payload.user_text.strip()
        query = " ".join(
            value for value in (fragment_query, user_query) if value
        )
        if not query:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "search_query_required",
                    "user_message": "请选择分析片段或输入检索内容。",
                },
            )
        response: dict[str, Any] = {"plans": {}}
        if "media_library" in payload.sources:
            outcome = await ctx.media_library_search_service.plan(
                MediaLibrarySearchRequest(
                    query=query,
                    dialogue_query=fragment_query,
                    user_query=user_query,
                    entry_point="editor",
                    query_source=("dialogue" if payload.fragment_refs else "manual"),
                    target_task_id=payload.target_task_id,
                    source_asset_id=asset_id,
                    orientation=payload.orientation,
                    sources=["media_library"],
                    limit=payload.limit,
                )
            )
            response.update(
                {
                    "plan": outcome.plan.model_dump(mode="json"),
                    "planner_degraded": outcome.degraded,
                    "planner_latency_ms": outcome.latency_ms,
                    "error_code": outcome.error_code,
                }
            )
            response["plans"]["media_library"] = response["plan"]
        if "external" in payload.sources:
            external = _external_search_payload(
                query=query,
                orientation=payload.orientation,
                limit=payload.limit,
            )["plan"]
            response["plans"]["external"] = external
            response.setdefault("plan", external)
            response.setdefault("planner_degraded", False)
            response.setdefault("planner_latency_ms", 0)
            response.setdefault("error_code", None)
        return response

    @router.post("/api/media-library/{asset_id}/search/runs")
    async def editor_search_run(asset_id: str, payload: EditorMediaSearchInput) -> dict[str, Any]:
        require_media_library_feature("editor")
        require_media_library_feature("library_search")
        if "external" in payload.sources and payload.target_task_id is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "search_target_task_required",
                    "user_message": "启用外部素材来源前必须选择目标 StoryBoard Task。",
                },
            )
        fragment_query = (
            _editor_fragment_query(ctx, asset_id, payload.fragment_refs, "")
            if payload.fragment_refs
            else ""
        )
        user_query = payload.user_text.strip()
        query = " ".join(
            value for value in (fragment_query, user_query) if value
        )
        if not query:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "search_query_required",
                    "user_message": "请选择分析片段或输入检索内容。",
                },
            )
        search_request = MediaLibrarySearchRequest(
            query=query,
            dialogue_query=fragment_query,
            user_query=user_query,
            entry_point="editor",
            query_source=("dialogue" if payload.fragment_refs else "manual"),
            target_task_id=payload.target_task_id,
            source_asset_id=asset_id,
            orientation=payload.orientation,
            sources=list(payload.sources),
            limit=payload.limit,
        )
        started = await ctx.media_library_search_service.begin_search(search_request)
        media_task = ctx.media_library_search_service.retrieve_started(started) if "media_library" in payload.sources else None
        external_task = (
            _external_editor_search(
                ctx,
                target_task_id=int(payload.target_task_id or 0),
                query=query,
                orientation=payload.orientation,
                limit=payload.limit,
            )
            if "external" in payload.sources
            else None
        )
        pending = [task for task in (media_task, external_task) if task is not None]
        completed = await asyncio.gather(*pending, return_exceptions=True)
        cursor = 0
        media_payload: dict[str, Any] | None = None
        external_payload: dict[str, Any] | None = None
        errors: dict[str, Any] = {}
        source_runs: dict[str, str | None] = {
            "media_library": (started.search_id if "media_library" in payload.sources else None),
            "external": None,
        }
        if media_task is not None:
            result = completed[cursor]
            cursor += 1
            if isinstance(result, BaseException):
                errors["media_library"] = {
                    "code": "media_library_search_failed",
                    "user_message": str(result),
                }
            else:
                media_payload = result.model_dump(mode="json")
        if external_task is not None:
            result = completed[cursor]
            if isinstance(result, HTTPException):
                errors["external"] = (
                    result.detail
                    if isinstance(result.detail, dict)
                    else {
                        "code": "external_search_failed",
                        "user_message": str(result.detail),
                    }
                )
            elif isinstance(result, BaseException):
                errors["external"] = {
                    "code": "external_search_failed",
                    "user_message": str(result),
                }
            else:
                external_payload = result
                source_runs["external"] = str(external_payload.get("search_id") or "") or None
        if media_payload is None and external_payload is None:
            ctx.media_library_search_service.fail_editor_run(
                started,
                source_runs=source_runs,
                source_errors=errors,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "editor_search_failed",
                    "user_message": "所选素材来源均未能完成检索。",
                    "source_errors": errors,
                    "search_id": started.search_id,
                },
            )
        media_items = list((media_payload or {}).get("items") or [])
        external_items = list((external_payload or {}).get("items") or [])
        items = [*media_items, *external_items]
        ctx.media_library_search_service.complete_editor_run(
            started,
            candidates=items,
            source_runs=source_runs,
            source_errors=errors,
        )
        return {
            **(media_payload or {}),
            "search_id": started.search_id,
            "retrieval_version": str((media_payload or {}).get("retrieval_version") or "dialogue_visual_literal_v1"),
            "planner_version": str((media_payload or {}).get("planner_version") or started.outcome.plan.planner_version),
            "planner_degraded": bool((media_payload or {}).get("planner_degraded", started.outcome.degraded)),
            "search_runs": source_runs,
            "requested_sources": list(payload.sources),
            "items": items,
            "result_count": len(items),
            "total_count": len(items),
            "limit": payload.limit,
            "offset": 0,
            "source_errors": errors,
            "external_provider_stats": (external_payload or {}).get("provider_stats", {}),
        }

    @router.get("/api/media-library/{asset_id}/search/runs/{search_id}")
    async def editor_search_replay(asset_id: str, search_id: str) -> dict[str, Any]:
        return _replay_response(
            ctx,
            search_id,
            {"entry_point": "editor", "source_asset_id": asset_id},
        )

    @router.post("/api/media-library/{asset_id}/search/runs/{search_id}/actions")
    async def editor_search_action(
        asset_id: str,
        search_id: str,
        payload: EditorSearchActionInput,
    ) -> dict[str, Any]:
        require_media_library_feature("editor")
        require_media_library_feature("library_search")
        run = _run_for_context(
            ctx,
            search_id,
            {"entry_point": "editor", "source_asset_id": asset_id},
        )
        if str(run.get("status") or "") != "completed":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "search_run_not_completed",
                    "user_message": "检索运行尚未完成，不能记录候选操作。",
                },
            )
        snapshot = next(
            (
                item
                for item in run.get("top_candidates_json") or []
                if isinstance(item, dict) and str(item.get("source") or "") == payload.source and str(item.get("candidate_id") or "") == payload.candidate_id
            ),
            None,
        )
        if snapshot is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "search_candidate_not_found",
                    "user_message": "候选不属于该检索快照。",
                },
            )
        if payload.action_kind == "open_editor" and payload.source != "media_library":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "search_action_not_allowed",
                    "user_message": "外部候选不能打开素材库剪辑页。",
                },
            )
        recorded = await asyncio.to_thread(
            ctx.media_library_search_service.record_action,
            MediaLibrarySearchAction(
                search_id=search_id,
                action_kind=payload.action_kind,
                source=payload.source,
                candidate_id=payload.candidate_id,
                target_task_id=run.get("target_task_id"),
                metadata=payload.metadata,
            ),
        )
        return {"ok": True, "recorded": bool(recorded)}

    return router
