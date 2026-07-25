from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Text, and_, bindparam, cast, func, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine

from ..db.schema import (
    media_library_analysis_runs,
    media_library_assets,
    media_library_clip_derivatives,
    media_library_fragment_index,
    media_library_search_actions,
    media_library_search_runs,
    media_library_tasks,
    session_files,
)
from ..media_library_features import (
    media_library_feature_state,
    require_media_library_feature,
)
from ..media_library_status import (
    derive_asset_status,
    derive_task_status,
    derive_visual_status,
)
from ..repositories.base import row_to_dict
from .normalization import (
    NORMALIZATION_VERSION,
    normalize_text,
    normalized_search_text,
    normalized_unique,
)
from .schemas import MediaLibraryFragmentV1, MediaLibraryQueryPlanV1


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TransactionHook = Callable[[Connection, str], None]


def _contains_bound_term(
    dialect_name: str, column: Any, parameter_name: str
) -> Any:
    if dialect_name == "postgresql":
        return func.strpos(column, bindparam(parameter_name)) > 0
    return func.instr(column, bindparam(parameter_name)) > 0


def _apply_term_filters(
    statement: Any,
    *,
    dialect_name: str,
    searchable_columns: Iterable[Any],
    recall_terms: Iterable[str],
    negative_terms: Iterable[str],
    parameter_prefix: str,
) -> tuple[Any, dict[str, str]]:
    columns = tuple(searchable_columns)
    parameters: dict[str, str] = {}
    recall_conditions = []
    for index, term in enumerate(recall_terms):
        name = f"{parameter_prefix}recall_{index}"
        parameters[name] = term
        recall_conditions.append(
            or_(
                *(
                    _contains_bound_term(dialect_name, column, name)
                    for column in columns
                )
            )
        )
    if recall_conditions:
        statement = statement.where(or_(*recall_conditions))
    for index, term in enumerate(negative_terms):
        name = f"{parameter_prefix}negative_{index}"
        parameters[name] = term
        statement = statement.where(
            and_(
                *(
                    ~_contains_bound_term(dialect_name, column, name)
                    for column in columns
                )
            )
        )
    return statement, parameters


def _apply_orientation_filter(
    statement: Any,
    orientation: str,
    *,
    width_column: Any,
    height_column: Any,
) -> Any:
    if orientation == "portrait":
        return statement.where(height_column > width_column)
    if orientation == "landscape":
        return statement.where(width_column >= height_column)
    return statement


def _orientation_matches(
    requested_orientation: str, width: Any, height: Any
) -> bool:
    return (
        requested_orientation != "any"
        and _orientation(width, height) == requested_orientation
    )


def _safe_reference(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        candidate = value.strip().replace("\\", "/")
        if not candidate:
            return True
        if (
            candidate.startswith("/")
            or candidate.startswith("../")
            or "/../" in candidate
            or "://" in candidate
            or re.match(r"^[a-zA-Z]:/", candidate)
        ):
            return False
        return True
    if isinstance(value, list):
        return all(_safe_reference(item) for item in value)
    if isinstance(value, dict):
        return all(_safe_reference(item) for item in value.values())
    return isinstance(value, (int, float, bool))


def _coerce_fragment(raw: MediaLibraryFragmentV1 | Mapping[str, Any]) -> MediaLibraryFragmentV1:
    if isinstance(raw, MediaLibraryFragmentV1):
        return raw
    value = dict(raw)
    duration = value.pop("duration_ms", None)
    needs_review = bool(value.pop("needs_review", False))
    if "keyframe_refs" in value and "keyframe_ref" not in value:
        value["keyframe_ref"] = value.pop("keyframe_refs")
    if "keywords_json" in value and "keywords" not in value:
        value["keywords"] = value.pop("keywords_json")
    if "visual_labels_json" in value and "visual_labels" not in value:
        value["visual_labels"] = value.pop("visual_labels_json")
    if needs_review and "quality_status" not in value:
        value["quality_status"] = "review"
    fragment = MediaLibraryFragmentV1.model_validate(value)
    if duration is not None and (
        isinstance(duration, bool)
        or not isinstance(duration, int)
        or duration != fragment.end_ms - fragment.start_ms
    ):
        raise ValueError("fragment_duration_ms_invalid")
    return fragment


class MediaLibraryFragmentPublisher:
    """Atomically replace the active fragment set for one business run."""

    def __init__(
        self,
        engine: Engine,
        *,
        transaction_hook: TransactionHook | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        metric_sink: Callable[[str, int], None] | None = None,
    ) -> None:
        self.engine = engine
        self.transaction_hook = transaction_hook
        self.event_sink = event_sink
        self.metric_sink = metric_sink

    def publish_dialogue(
        self,
        *,
        asset_id: str,
        analysis_run_id: str,
        result_hash: str,
        fragments: Iterable[MediaLibraryFragmentV1 | Mapping[str, Any]],
        timestamp: int,
        schema_version: str = "media_library_dialogue_fragments_v1",
        result_index_path: str | None = None,
        progress: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.publish(
            asset_id=asset_id,
            analysis_run_id=analysis_run_id,
            analysis_scheme="dialogue",
            result_hash=result_hash,
            fragments=fragments,
            timestamp=timestamp,
            schema_version=schema_version,
            result_index_path=result_index_path,
            progress=progress,
        )

    def publish_visual_semantic(
        self,
        *,
        asset_id: str,
        analysis_run_id: str,
        result_hash: str,
        fragments: Iterable[MediaLibraryFragmentV1 | Mapping[str, Any]],
        timestamp: int,
        result_index_path: str,
        visual_structure_run_id: str,
        visual_structure_result_hash: str,
        progress: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.publish(
            asset_id=asset_id,
            analysis_run_id=analysis_run_id,
            analysis_scheme="visual_semantic",
            result_hash=result_hash,
            fragments=fragments,
            timestamp=timestamp,
            schema_version="media_library_visual_semantic_v2",
            result_index_path=result_index_path,
            progress=progress,
            sampling_strategy="scene_uniform_4_v1",
            expected_upstream_refs={
                "visual_structure": {
                    "analysis_run_id": visual_structure_run_id,
                    "result_hash": visual_structure_result_hash,
                }
            },
        )

    def backfill_visual_semantic(
        self,
        *,
        asset_id: str,
        analysis_run_id: str,
        result_hash: str,
        fragments: Iterable[MediaLibraryFragmentV1 | Mapping[str, Any]],
        timestamp: int,
        result_index_path: str,
        visual_structure_run_id: str,
        visual_structure_result_hash: str,
    ) -> dict[str, Any]:
        return self.publish(
            asset_id=asset_id,
            analysis_run_id=analysis_run_id,
            analysis_scheme="visual_semantic",
            result_hash=result_hash,
            fragments=fragments,
            timestamp=timestamp,
            schema_version="media_library_visual_semantic_v2",
            result_index_path=result_index_path,
            sampling_strategy="scene_uniform_4_v1",
            expected_upstream_refs={
                "visual_structure": {
                    "analysis_run_id": visual_structure_run_id,
                    "result_hash": visual_structure_result_hash,
                }
            },
            _allow_current_ready_backfill=True,
        )

    def publish(
        self,
        *,
        asset_id: str,
        analysis_run_id: str,
        analysis_scheme: str,
        result_hash: str,
        fragments: Iterable[MediaLibraryFragmentV1 | Mapping[str, Any]],
        timestamp: int,
        schema_version: str,
        result_index_path: str | None = None,
        progress: Mapping[str, Any] | None = None,
        expected_upstream_refs: Mapping[
            str, Mapping[str, str]
        ] | None = None,
        sampling_strategy: str | None = None,
        _allow_current_ready_backfill: bool = False,
    ) -> dict[str, Any]:
        require_media_library_feature("analysis_runs")
        if analysis_scheme not in {
            "dialogue",
            "visual_semantic",
            "composite",
        }:
            raise ValueError("fragment_analysis_scheme_invalid")
        if analysis_scheme == "visual_semantic":
            require_media_library_feature("visual_search_v1")
            if (
                schema_version != "media_library_visual_semantic_v2"
                or sampling_strategy != "scene_uniform_4_v1"
            ):
                raise ValueError("sampling_strategy_ineligible")
            if set(expected_upstream_refs or {}) != {"visual_structure"}:
                raise ValueError("visual_structure_upstream_required")
        if not SHA256_RE.fullmatch(result_hash):
            raise ValueError("fragment_result_hash_invalid")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise ValueError("fragment_timestamp_integer_ms_required")
        prepared = [_coerce_fragment(item) for item in fragments]
        if not prepared and analysis_scheme != "dialogue":
            raise ValueError("fragment_set_empty")
        fragment_ids = [fragment.fragment_id for fragment in prepared]
        if len(fragment_ids) != len(set(fragment_ids)):
            raise ValueError("fragment_id_duplicate")
        for fragment in prepared:
            if (
                analysis_scheme == "dialogue"
                and not normalize_text(fragment.dialogue_text)
            ):
                raise ValueError("dialogue_fragment_text_empty")
            if not _safe_reference(fragment.keyframe_ref):
                raise ValueError("fragment_keyframe_reference_unsafe")
            if analysis_scheme == "visual_semantic":
                expected_keyframes = [
                    f"{fragment.fragment_id}-sample-{index:02d}"
                    for index in range(1, 5)
                ]
                if (
                    fragment.dialogue_text is not None
                    or fragment.title is not None
                    or fragment.keyframe_ref != expected_keyframes
                ):
                    raise ValueError("visual_semantic_fragment_ineligible")

        stale_composite_event: dict[str, Any] | None = None
        with self.engine.begin() as conn:
            asset = row_to_dict(
                conn.execute(
                    select(media_library_assets)
                    .where(media_library_assets.c.asset_id == asset_id)
                    .with_for_update()
                ).first()
            )
            if asset is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "media_asset_not_found",
                        "user_message": "素材不存在或已删除。",
                    },
                )
            source_version = str(asset.get("content_sha256") or "")
            if not SHA256_RE.fullmatch(source_version):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "media_source_identity_missing",
                        "user_message": "素材缺少稳定内容哈希，不能发布检索索引。",
                    },
                )
            run = row_to_dict(
                conn.execute(
                    select(media_library_analysis_runs)
                    .where(
                        media_library_analysis_runs.c.analysis_run_id
                        == analysis_run_id
                    )
                    .with_for_update()
                ).first()
            )
            if run is None or str(run["asset_id"]) != asset_id:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "analysis_run_not_found",
                        "user_message": "分析运行不存在。",
                    },
                )
            if (
                str(run["scheme"]) != analysis_scheme
                or str(run["source_version"]) != source_version
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "media_source_version_mismatch",
                        "user_message": "分析运行与当前素材版本不一致。",
                    },
                )
            duration_ms = asset.get("duration_ms")
            if duration_ms is not None:
                for fragment in prepared:
                    if fragment.end_ms > int(duration_ms):
                        raise ValueError("fragment_time_range_exceeds_source")

            existing = [
                dict(row)
                for row in conn.execute(
                    select(media_library_fragment_index).where(
                        media_library_fragment_index.c.analysis_run_id
                        == analysis_run_id
                    )
                )
                .mappings()
                .fetchall()
            ]
            if existing:
                if self._is_idempotent_ready_publication(
                    run=run,
                    existing=existing,
                    prepared=prepared,
                    result_hash=result_hash,
                ):
                    return {
                        "analysis_run_id": analysis_run_id,
                        "asset_id": asset_id,
                        "analysis_scheme": analysis_scheme,
                        "result_hash": result_hash,
                        "fragment_count": len(existing),
                        "idempotent": True,
                    }
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "fragment_publication_conflict",
                        "user_message": "同一分析运行已经发布了不同的 fragment 集合。",
                    },
                )
            if (
                not prepared
                and str(run.get("status") or "") == "ready"
                and bool(run.get("is_current"))
                and str(run.get("result_hash") or "") == result_hash
            ):
                return {
                    "analysis_run_id": analysis_run_id,
                    "asset_id": asset_id,
                    "analysis_scheme": analysis_scheme,
                    "result_hash": result_hash,
                    "fragment_count": 0,
                    "idempotent": True,
                }
            ready_backfill = bool(
                _allow_current_ready_backfill
                and analysis_scheme == "visual_semantic"
                and str(run.get("status") or "") == "ready"
                and bool(run.get("is_current"))
                and str(run.get("schema_version") or "")
                == "media_library_visual_semantic_v2"
                and str(run.get("result_hash") or "") == result_hash
                and str(run.get("result_index_path") or "")
                == str(result_index_path or "")
            )
            if (
                str(run["status"]) not in {"queued", "running"}
                and not ready_backfill
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "analysis_result_exists",
                        "user_message": "该分析运行不能再次激活检索索引。",
                    },
                )
            for upstream_scheme, expected in (
                expected_upstream_refs or {}
            ).items():
                upstream = row_to_dict(
                    conn.execute(
                        select(media_library_analysis_runs)
                        .where(
                            media_library_analysis_runs.c.asset_id
                            == asset_id,
                            media_library_analysis_runs.c.scheme
                            == str(upstream_scheme),
                            media_library_analysis_runs.c.is_current.is_(
                                True
                            ),
                            media_library_analysis_runs.c.status == "ready",
                        )
                        .with_for_update()
                    ).first()
                )
                if (
                    upstream is None
                    or str(upstream["analysis_run_id"])
                    != str(expected.get("analysis_run_id") or "")
                    or str(upstream.get("result_hash") or "")
                    != str(expected.get("result_hash") or "")
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "analysis_upstream_changed",
                            "user_message": "综合分析运行期间上游结果已更新，本次结果不会发布。",
                            "suggested_action": "基于当前上游结果重新运行综合分析。",
                        },
                    )

            rows = [
                self._fragment_row(
                    fragment,
                    asset=asset,
                    analysis_run_id=analysis_run_id,
                    analysis_scheme=analysis_scheme,
                    result_hash=result_hash,
                    timestamp=timestamp,
                )
                for fragment in prepared
            ]
            if rows:
                conn.execute(insert(media_library_fragment_index), rows)
            self._hook(conn, "inserted_inactive")
            if (
                not ready_backfill
                and analysis_scheme in {"dialogue", "visual_semantic"}
            ):
                stale_composite = row_to_dict(
                    conn.execute(
                        select(media_library_analysis_runs)
                        .where(
                            media_library_analysis_runs.c.asset_id
                            == asset_id,
                            media_library_analysis_runs.c.scheme
                            == "composite",
                            media_library_analysis_runs.c.is_current.is_(
                                True
                            ),
                            media_library_analysis_runs.c.status == "ready",
                        )
                        .with_for_update()
                    ).first()
                )
                if stale_composite is not None:
                    stale_composite_event = {
                        "analysis_run_id": str(
                            stale_composite["analysis_run_id"]
                        ),
                        "asset_id": asset_id,
                        "scheme": "composite",
                        "status": "stale",
                    }
                    conn.execute(
                        update(media_library_analysis_runs)
                        .where(
                            media_library_analysis_runs.c.analysis_run_id
                            == stale_composite["analysis_run_id"]
                        )
                        .values(
                            status="stale",
                            error_code="analysis_upstream_changed",
                            error_json={
                                "code": "analysis_upstream_changed",
                                "user_message": (
                                    "对白分析已更新，本综合结果仅保留为只读版本。"
                                    if analysis_scheme == "dialogue"
                                    else "画面语义已更新，本综合结果仅保留为只读版本。"
                                ),
                                "suggested_action": "重新运行综合分析。",
                                "upstream_scheme": analysis_scheme,
                                "upstream_run_id": analysis_run_id,
                            },
                            updated_at=timestamp,
                        )
                    )
                    conn.execute(
                        update(media_library_fragment_index)
                        .where(
                            media_library_fragment_index.c.analysis_run_id
                            == stale_composite["analysis_run_id"],
                            media_library_fragment_index.c.is_active.is_(
                                True
                            ),
                        )
                        .values(is_active=False, updated_at=timestamp)
                    )
                    conn.execute(
                        update(media_library_tasks)
                        .where(
                            media_library_tasks.c.asset_id == asset_id
                        )
                        .values(
                            composite_status="stale",
                            composite_current_run_id=str(
                                stale_composite["analysis_run_id"]
                            ),
                            composite_error=(
                                "对白分析已更新，本综合结果需要重新运行。"
                                if analysis_scheme == "dialogue"
                                else "画面语义已更新，本综合结果需要重新运行。"
                            ),
                            updated_at=timestamp,
                        )
                    )
            conn.execute(
                update(media_library_fragment_index)
                .where(
                    media_library_fragment_index.c.asset_id == asset_id,
                    media_library_fragment_index.c.analysis_scheme
                    == analysis_scheme,
                    media_library_fragment_index.c.analysis_run_id
                    != analysis_run_id,
                    media_library_fragment_index.c.is_active.is_(True),
                )
                .values(is_active=False, updated_at=timestamp)
            )
            self._hook(conn, "old_deactivated")
            conn.execute(
                update(media_library_fragment_index)
                .where(
                    media_library_fragment_index.c.analysis_run_id
                    == analysis_run_id
                )
                .values(is_active=True, updated_at=timestamp)
            )
            conn.execute(
                update(media_library_analysis_runs)
                .where(
                    media_library_analysis_runs.c.asset_id == asset_id,
                    media_library_analysis_runs.c.scheme == analysis_scheme,
                    media_library_analysis_runs.c.is_current.is_(True),
                    media_library_analysis_runs.c.analysis_run_id
                    != analysis_run_id,
                )
                .values(is_current=False, updated_at=timestamp)
            )
            if not ready_backfill:
                activation = conn.execute(
                    update(media_library_analysis_runs)
                    .where(
                        media_library_analysis_runs.c.analysis_run_id
                        == analysis_run_id,
                        media_library_analysis_runs.c.status.in_(
                            ("queued", "running")
                        ),
                    )
                    .values(
                        status="ready",
                        schema_version=schema_version,
                        result_hash=result_hash,
                        result_index_path=result_index_path,
                        progress_json=(
                            dict(progress)
                            if progress is not None
                            else run.get("progress_json") or {}
                        ),
                        upstream_refs_json=(
                            {
                                "visual_structure_run_id": str(
                                    (expected_upstream_refs or {})[
                                        "visual_structure"
                                    ]["analysis_run_id"]
                                ),
                                "visual_structure_result_hash": str(
                                    (expected_upstream_refs or {})[
                                        "visual_structure"
                                    ]["result_hash"]
                                ),
                            }
                            if analysis_scheme == "visual_semantic"
                            else run.get("upstream_refs_json")
                            or dict(expected_upstream_refs or {})
                        ),
                        is_current=True,
                        error_code=None,
                        error_json=None,
                        finished_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                if activation.rowcount != 1:
                    raise RuntimeError("fragment_run_activation_failed")
            self._update_projection(
                conn,
                asset=asset,
                scheme=analysis_scheme,
                analysis_run_id=analysis_run_id,
                fragment_count=len(rows),
                timestamp=timestamp,
                progress=progress,
            )
            self._hook(conn, "ready_activated")
        self._event(
            "media_library.fragment_index.published",
            {
                "analysis_run_id": analysis_run_id,
                "asset_id": asset_id,
                "analysis_scheme": analysis_scheme,
                "fragment_count": len(prepared),
                "result_hash": result_hash,
            },
        )
        self._event(
            "media_library.analysis.run.ready",
            {
                "analysis_run_id": analysis_run_id,
                "asset_id": asset_id,
                "scheme": analysis_scheme,
                "status": "ready",
            },
        )
        self._metric(scheme=analysis_scheme, status="ready")
        if stale_composite_event is not None:
            self._event(
                "media_library.analysis.run.stale",
                stale_composite_event,
            )
            self._metric(scheme="composite", status="stale")
        return {
            "analysis_run_id": analysis_run_id,
            "asset_id": asset_id,
            "analysis_scheme": analysis_scheme,
            "result_hash": result_hash,
            "fragment_count": len(prepared),
            "idempotent": False,
        }

    def _hook(self, conn: Connection, phase: str) -> None:
        if self.transaction_hook is not None:
            self.transaction_hook(conn, phase)

    def _event(self, kind: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(kind, payload)
        except Exception:
            # Index publication is authoritative; telemetry is best effort.
            return

    def _metric(self, *, scheme: str, status: str) -> None:
        if self.metric_sink is None:
            return
        try:
            self.metric_sink(
                "media_library_analysis_total"
                f'{{scheme="{scheme}",status="{status}"}}',
                1,
            )
        except Exception:
            # Index publication is authoritative; metrics are best effort.
            return

    @staticmethod
    def _fragment_row(
        fragment: MediaLibraryFragmentV1,
        *,
        asset: Mapping[str, Any],
        analysis_run_id: str,
        analysis_scheme: str,
        result_hash: str,
        timestamp: int,
    ) -> dict[str, Any]:
        keywords = normalized_unique(fragment.keywords)
        labels = normalized_unique(fragment.visual_labels)
        return {
            "asset_id": str(asset["asset_id"]),
            "source_session_id": int(asset["session_id"]),
            "source_version": str(asset["content_sha256"]),
            "analysis_scheme": analysis_scheme,
            "analysis_run_id": analysis_run_id,
            "result_hash": result_hash,
            "fragment_id": fragment.fragment_id,
            "start_ms": fragment.start_ms,
            "end_ms": fragment.end_ms,
            "dialogue_text": (
                str(fragment.dialogue_text).strip()
                if fragment.dialogue_text is not None
                else None
            ),
            "title": str(fragment.title).strip() if fragment.title else None,
            "summary": str(fragment.summary).strip() if fragment.summary else None,
            "keywords_json": keywords,
            "visual_labels_json": labels,
            "keyframe_ref_json": fragment.keyframe_ref,
            "search_text": normalized_search_text(
                fragment.dialogue_text,
                fragment.title,
                fragment.summary,
                keywords,
                labels,
            ),
            "search_lexemes_text": None,
            "tokenizer_name": "none",
            "tokenizer_version": "none",
            "dictionary_hash": None,
            "normalization_version": NORMALIZATION_VERSION,
            "quality_status": fragment.quality_status,
            "confidence": fragment.confidence,
            "is_active": False,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

    @staticmethod
    def _is_idempotent_ready_publication(
        *,
        run: Mapping[str, Any],
        existing: list[dict[str, Any]],
        prepared: list[MediaLibraryFragmentV1],
        result_hash: str,
    ) -> bool:
        if (
            str(run.get("status")) != "ready"
            or not bool(run.get("is_current"))
            or str(run.get("result_hash") or "") != result_hash
            or len(existing) != len(prepared)
        ):
            return False
        by_id = {str(row["fragment_id"]): row for row in existing}
        for fragment in prepared:
            row = by_id.get(fragment.fragment_id)
            if row is None:
                return False
            if (
                int(row["start_ms"]) != fragment.start_ms
                or int(row["end_ms"]) != fragment.end_ms
                or normalize_text(row.get("dialogue_text"))
                != normalize_text(fragment.dialogue_text)
                or normalize_text(row.get("title"))
                != normalize_text(fragment.title)
                or normalize_text(row.get("summary"))
                != normalize_text(fragment.summary)
                or normalized_unique(row.get("keywords_json") or [])
                != normalized_unique(fragment.keywords)
                or normalized_unique(row.get("visual_labels_json") or [])
                != normalized_unique(fragment.visual_labels)
                or row.get("keyframe_ref_json") != fragment.keyframe_ref
                or str(row.get("quality_status") or "")
                != fragment.quality_status
                or (
                    None
                    if row.get("confidence") is None
                    else float(row["confidence"])
                )
                != fragment.confidence
                or str(row.get("result_hash") or "") != result_hash
                or not bool(row.get("is_active"))
            ):
                return False
        return True

    @staticmethod
    def _update_projection(
        conn: Connection,
        *,
        asset: Mapping[str, Any],
        scheme: str,
        analysis_run_id: str,
        fragment_count: int,
        timestamp: int,
        progress: Mapping[str, Any] | None,
    ) -> None:
        task = row_to_dict(
            conn.execute(
                select(media_library_tasks)
                .where(media_library_tasks.c.asset_id == asset["asset_id"])
                .with_for_update()
            ).first()
        )
        if task is None:
            raise RuntimeError("media_library_task_missing")
        values: dict[str, Any]
        if scheme == "dialogue":
            values = {
                "dialogue_status": "ready",
                "dialogue_current_run_id": analysis_run_id,
                "dialogue_error": None,
            }
            if progress is not None:
                values["dialogue_progress_json"] = dict(progress)
            task["dialogue_status"] = "ready"
        elif scheme == "visual_semantic":
            visual_status = derive_visual_status(
                str(task.get("visual_structure_status") or "not_analyzed"),
                "ready",
            )
            values = {
                "visual_semantic_status": "ready",
                "visual_semantic_current_run_id": analysis_run_id,
                "visual_semantic_error": None,
                "visual_status": visual_status,
            }
            if progress is not None:
                values["visual_semantic_progress_json"] = dict(progress)
            task["visual_semantic_status"] = "ready"
            task["visual_status"] = visual_status
        else:
            values = {
                "composite_status": "ready",
                "composite_current_run_id": analysis_run_id,
                "composite_error": None,
            }
            if progress is not None:
                values["composite_progress_json"] = dict(progress)
            task["composite_status"] = "ready"
        values["status"] = derive_task_status(task)
        values["updated_at"] = timestamp
        conn.execute(
            update(media_library_tasks)
            .where(media_library_tasks.c.asset_id == asset["asset_id"])
            .values(**values)
        )
        summary = dict(asset.get("analysis_summary_json") or {})
        summary[f"{scheme}_fragment_count"] = fragment_count
        for key in ("keep_count", "review_count"):
            summary.pop(key, None)
        quality_counts = {
            str(status): int(count)
            for status, count in conn.execute(
                select(
                    media_library_fragment_index.c.quality_status,
                    func.count(),
                )
                .where(
                    media_library_fragment_index.c.asset_id == asset["asset_id"],
                    media_library_fragment_index.c.is_active.is_(True),
                )
                .group_by(media_library_fragment_index.c.quality_status)
            ).all()
        }
        if quality_counts:
            summary.update(
                keep_count=quality_counts.get("ready", 0),
                review_count=quality_counts.get("review", 0),
            )
        conn.execute(
            update(media_library_assets)
            .where(media_library_assets.c.asset_id == asset["asset_id"])
            .values(
                analysis_status=derive_asset_status(task),
                analysis_summary_json=summary,
                updated_at=timestamp,
            )
        )


class MediaLibrarySearchRepository:
    MAX_RECALLED_FRAGMENTS = 300
    MAX_FRAGMENTS_PER_ASSET = 3

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _visual_search_enabled() -> bool:
        state = media_library_feature_state("visual_search_v1")
        return state.configuration_valid and state.enabled

    @staticmethod
    def _clip_search_enabled() -> bool:
        state = media_library_feature_state("clip_search_v1")
        return state.configuration_valid and state.enabled

    @staticmethod
    def _scheme_eligibility(visual_enabled: bool) -> Any:
        dialogue = and_(
            media_library_fragment_index.c.analysis_scheme == "dialogue",
            media_library_tasks.c.dialogue_status == "ready",
        )
        if not visual_enabled:
            return dialogue
        return or_(
            dialogue,
            and_(
                media_library_fragment_index.c.analysis_scheme
                == "visual_semantic",
                media_library_tasks.c.visual_semantic_status == "ready",
                media_library_analysis_runs.c.schema_version
                == "media_library_visual_semantic_v2",
            ),
        )

    @staticmethod
    def _row_scheme_eligible(
        row: Mapping[str, Any], *, visual_enabled: bool
    ) -> bool:
        scheme = str(row.get("analysis_scheme") or "")
        if scheme == "dialogue":
            return str(row.get("task_dialogue_status") or "") == "ready"
        if not visual_enabled or scheme != "visual_semantic":
            return False
        if (
            str(row.get("task_visual_semantic_status") or "") != "ready"
            or str(row.get("run_schema_version") or "")
            != "media_library_visual_semantic_v2"
        ):
            return False
        fragment_id = str(row.get("fragment_id") or "")
        return row.get("keyframe_ref_json") == [
            f"{fragment_id}-sample-{index:02d}" for index in range(1, 5)
        ]

    @staticmethod
    def _visual_keyframe_refs_eligibility() -> Any:
        refs = media_library_fragment_index.c.keyframe_ref_json
        fragment_id = media_library_fragment_index.c.fragment_id
        return and_(
            func.json_array_length(refs) == 4,
            *(
                refs[index - 1].as_string()
                == fragment_id + f"-sample-{index:02d}"
                for index in range(1, 5)
            ),
        )

    def capacity(self) -> dict[str, int]:
        visual_enabled = self._visual_search_enabled()
        eligible = (
            media_library_assets.c.upload_status == "ready",
            media_library_assets.c.archived.is_(False),
            media_library_assets.c.content_sha256.is_not(None),
        )
        with self.engine.connect() as conn:
            ready_assets = int(
                conn.execute(
                    select(func.count())
                    .select_from(media_library_assets)
                    .where(*eligible)
                ).scalar_one()
            )
            fragment_counts = {
                str(row.analysis_scheme): int(row.fragment_count)
                for row in conn.execute(
                    select(
                        media_library_fragment_index.c.analysis_scheme,
                        func.count().label("fragment_count"),
                    )
                    .select_from(
                        media_library_fragment_index.join(
                            media_library_assets,
                            media_library_assets.c.asset_id
                            == media_library_fragment_index.c.asset_id,
                        )
                        .join(
                            media_library_tasks,
                            media_library_tasks.c.asset_id
                            == media_library_fragment_index.c.asset_id,
                        )
                        .join(
                            media_library_analysis_runs,
                            media_library_analysis_runs.c.analysis_run_id
                            == media_library_fragment_index.c.analysis_run_id,
                        )
                    )
                    .where(
                        *eligible,
                        self._scheme_eligibility(visual_enabled),
                        or_(
                            media_library_fragment_index.c.analysis_scheme
                            == "dialogue",
                            and_(
                                media_library_fragment_index.c.analysis_scheme
                                == "visual_semantic",
                                self._visual_keyframe_refs_eligibility(),
                            ),
                        ),
                        media_library_fragment_index.c.is_active.is_(True),
                        media_library_analysis_runs.c.status == "ready",
                        media_library_analysis_runs.c.is_current.is_(True),
                        media_library_fragment_index.c.source_version
                        == media_library_assets.c.content_sha256,
                    )
                    .group_by(
                        media_library_fragment_index.c.analysis_scheme
                    )
                ).all()
            }
            eligible_clip_count = 0
            if self._clip_search_enabled():
                eligible_clip_count = int(
                    conn.execute(
                        select(func.count())
                        .select_from(
                            media_library_clip_derivatives.join(
                                media_library_assets,
                                media_library_assets.c.asset_id
                                == media_library_clip_derivatives.c.source_asset_id,
                            )
                        )
                        .where(
                            media_library_clip_derivatives.c.search_eligible.is_(
                                True
                            ),
                            media_library_assets.c.upload_status == "ready",
                            media_library_assets.c.archived.is_(False),
                            media_library_clip_derivatives.c.source_version
                            == media_library_assets.c.content_sha256,
                        )
                    ).scalar_one()
                )
        return {
            "ready_assets": ready_assets,
            "active_dialogue_fragments": fragment_counts.get("dialogue", 0),
            "active_visual_fragments": fragment_counts.get(
                "visual_semantic", 0
            ),
            "search_eligible_clips": eligible_clip_count,
        }

    def retrieve_clips(
        self,
        plan: MediaLibraryQueryPlanV1,
        *,
        exclude_asset_id: str | None = None,
        dialogue_query: str = "",
        user_query: str = "",
    ) -> list[dict[str, Any]]:
        if not self._clip_search_enabled():
            return []
        original = normalize_text(plan.original_query)
        dialogue_query = normalize_text(dialogue_query)
        user_query = normalize_text(user_query)
        exact_phrases = normalized_unique(plan.exact_phrases)
        optional_terms = normalized_unique(plan.optional_terms)
        negative_terms = normalized_unique(plan.negative_terms)
        recall_terms = normalized_unique(
            [
                user_query,
                dialogue_query,
                original,
                *exact_phrases,
                *optional_terms,
            ]
        )
        if not recall_terms:
            return []
        statement = (
            select(
                media_library_clip_derivatives,
                media_library_assets.c.width,
                media_library_assets.c.height,
                media_library_assets.c.updated_at.label("asset_updated_at"),
            )
            .select_from(
                media_library_clip_derivatives.join(
                    media_library_assets,
                    media_library_assets.c.asset_id
                    == media_library_clip_derivatives.c.source_asset_id,
                ).join(
                    session_files,
                    and_(
                        session_files.c.session_id
                        == media_library_clip_derivatives.c.source_session_id,
                        session_files.c.path
                        == media_library_clip_derivatives.c.output_path,
                    ),
                )
            )
            .where(
                media_library_clip_derivatives.c.search_eligible.is_(True),
                media_library_assets.c.upload_status == "ready",
                media_library_assets.c.archived.is_(False),
                media_library_assets.c.content_sha256.is_not(None),
                media_library_clip_derivatives.c.source_version
                == media_library_assets.c.content_sha256,
                func.length(media_library_clip_derivatives.c.content_sha256)
                == 64,
                session_files.c.kind == "video",
                session_files.c.stale == 0,
                session_files.c.downloadable != 0,
            )
        )
        if exclude_asset_id:
            statement = statement.where(
                media_library_clip_derivatives.c.source_asset_id
                != str(exclude_asset_id)
            )
        statement = _apply_orientation_filter(
            statement,
            plan.orientation,
            width_column=media_library_assets.c.width,
            height_column=media_library_assets.c.height,
        )
        if plan.min_duration_ms is not None:
            statement = statement.where(
                media_library_clip_derivatives.c.duration_ms
                >= plan.min_duration_ms
            )
        if plan.max_duration_ms is not None:
            statement = statement.where(
                media_library_clip_derivatives.c.duration_ms
                <= plan.max_duration_ms
            )
        with self.engine.connect() as conn:
            statement, parameters = _apply_term_filters(
                statement,
                dialect_name=conn.dialect.name,
                searchable_columns=(
                    media_library_clip_derivatives.c.search_text,
                ),
                recall_terms=recall_terms,
                negative_terms=negative_terms,
                parameter_prefix="clip_",
            )
            rows = [
                dict(row)
                for row in conn.execute(statement, parameters)
                .mappings()
                .fetchall()
            ]
        for row in rows:
            raw_score, reasons = self._score_clip(
                row,
                original=original,
                exact_phrases=exact_phrases,
                optional_terms=optional_terms,
                requested_orientation=plan.orientation,
                dialogue_query=dialogue_query,
                user_query=user_query,
            )
            row["_raw_score"] = raw_score
            row["_score_reasons"] = reasons
            row["candidate_updated_at"] = int(
                row.get("search_updated_at")
                or row.get("created_at")
                or 0
            )
        rows.sort(
            key=lambda row: (
                -float(row["_raw_score"]),
                -int(row.get("candidate_updated_at") or 0),
                str(row["clip_id"]),
            )
        )
        return rows[: self.MAX_RECALLED_FRAGMENTS]

    @staticmethod
    def _score_clip(
        row: Mapping[str, Any],
        *,
        original: str,
        exact_phrases: list[str],
        optional_terms: list[str],
        requested_orientation: str,
        dialogue_query: str = "",
        user_query: str = "",
    ) -> tuple[float, list[str]]:
        display_name = normalize_text(row.get("display_name"))
        tags = normalized_unique(row.get("tags_json") or [])
        name_phrases = normalized_unique(
            [user_query, dialogue_query, original, *exact_phrases]
        )
        tag_terms = normalized_unique(
            [*name_phrases, *optional_terms]
        )
        score = 0.0
        reasons: list[str] = []
        if any(phrase in display_name for phrase in name_phrases):
            score += 140
            reasons.append("片段完整名称命中")
        if any(term in tag for term in tag_terms for tag in tags):
            score += 110
            reasons.append("片段人工标签命中")
        if _orientation_matches(
            requested_orientation, row.get("width"), row.get("height")
        ):
            score += 5
            reasons.append("方向完全匹配")
        return score, reasons

    def recheck_clip_eligible(self, clip_ids: Iterable[str]) -> set[str]:
        if not self._clip_search_enabled():
            return set()
        ids = sorted({str(clip_id) for clip_id in clip_ids if clip_id})
        if not ids:
            return set()
        with self.engine.connect() as conn:
            return {
                str(row[0])
                for row in conn.execute(
                    select(media_library_clip_derivatives.c.clip_id)
                    .select_from(
                        media_library_clip_derivatives.join(
                            media_library_assets,
                            media_library_assets.c.asset_id
                            == media_library_clip_derivatives.c.source_asset_id,
                        )
                    )
                    .where(
                        media_library_clip_derivatives.c.clip_id.in_(ids),
                        media_library_clip_derivatives.c.search_eligible.is_(
                            True
                        ),
                        media_library_assets.c.upload_status == "ready",
                        media_library_assets.c.archived.is_(False),
                        media_library_clip_derivatives.c.source_version
                        == media_library_assets.c.content_sha256,
                    )
                ).all()
            }

    def retrieve(
        self,
        plan: MediaLibraryQueryPlanV1,
        *,
        exclude_asset_id: str | None = None,
        dialogue_query: str = "",
        user_query: str = "",
    ) -> list[dict[str, Any]]:
        visual_enabled = self._visual_search_enabled()
        original = normalize_text(plan.original_query)
        dialogue_query = normalize_text(dialogue_query)
        user_query = normalize_text(user_query)
        exact_phrases = normalized_unique(plan.exact_phrases)
        optional_terms = normalized_unique(plan.optional_terms)
        negative_terms = normalized_unique(plan.negative_terms)
        recall_terms = normalized_unique(
            [
                user_query,
                dialogue_query,
                original,
                *exact_phrases,
                *optional_terms,
            ]
        )
        if not recall_terms:
            return []

        statement = (
            select(
                media_library_fragment_index,
                media_library_assets.c.display_name,
                media_library_assets.c.thumbnail_url,
                media_library_assets.c.preview_url,
                media_library_assets.c.duration_ms,
                media_library_assets.c.width,
                media_library_assets.c.height,
                media_library_assets.c.tags_json,
                media_library_assets.c.updated_at.label("asset_updated_at"),
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
                    media_library_assets.c.asset_id
                    == media_library_fragment_index.c.asset_id,
                )
                .join(
                    media_library_tasks,
                    media_library_tasks.c.asset_id
                    == media_library_fragment_index.c.asset_id,
                )
                .join(
                    media_library_analysis_runs,
                    media_library_analysis_runs.c.analysis_run_id
                    == media_library_fragment_index.c.analysis_run_id,
                )
            )
            .where(
                media_library_assets.c.upload_status == "ready",
                media_library_assets.c.archived.is_(False),
                media_library_assets.c.content_sha256.is_not(None),
                self._scheme_eligibility(visual_enabled),
                media_library_fragment_index.c.is_active.is_(True),
                media_library_analysis_runs.c.status == "ready",
                media_library_analysis_runs.c.is_current.is_(True),
                media_library_fragment_index.c.source_version
                == media_library_assets.c.content_sha256,
            )
        )
        statement = _apply_orientation_filter(
            statement,
            plan.orientation,
            width_column=media_library_assets.c.width,
            height_column=media_library_assets.c.height,
        )
        if plan.min_duration_ms is not None:
            statement = statement.where(
                media_library_assets.c.duration_ms >= plan.min_duration_ms
            )
        if plan.max_duration_ms is not None:
            statement = statement.where(
                media_library_assets.c.duration_ms <= plan.max_duration_ms
            )
        if exclude_asset_id:
            statement = statement.where(
                media_library_fragment_index.c.asset_id
                != str(exclude_asset_id)
            )

        with self.engine.connect() as conn:
            searchable_columns = (
                media_library_fragment_index.c.search_text,
                func.lower(media_library_assets.c.display_name),
                func.lower(cast(media_library_assets.c.tags_json, Text)),
            )
            statement, parameters = _apply_term_filters(
                statement,
                dialect_name=conn.dialect.name,
                searchable_columns=searchable_columns,
                recall_terms=recall_terms,
                negative_terms=negative_terms,
                parameter_prefix="",
            )
            rows = [
                dict(row)
                for row in conn.execute(statement, parameters)
                .mappings()
                .fetchall()
            ]

        rows = [
            row
            for row in rows
            if self._row_scheme_eligible(
                row, visual_enabled=visual_enabled
            )
        ]

        for row in rows:
            raw_score, reasons = self._score_fragment(
                row,
                original=original,
                exact_phrases=exact_phrases,
                optional_terms=optional_terms,
                requested_orientation=plan.orientation,
                dialogue_query=dialogue_query,
                user_query=user_query,
            )
            row["_raw_score"] = raw_score
            row["_score_reasons"] = reasons
        rows.sort(
            key=lambda row: (
                -float(row["_raw_score"]),
                -int(row.get("asset_updated_at") or 0),
                str(row["asset_id"]),
                int(row["start_ms"]),
                str(row["fragment_id"]),
            )
        )
        per_asset: defaultdict[str, int] = defaultdict(int)
        kept: list[dict[str, Any]] = []
        for row in rows:
            asset_id = str(row["asset_id"])
            if per_asset[asset_id] >= self.MAX_FRAGMENTS_PER_ASSET:
                continue
            per_asset[asset_id] += 1
            kept.append(row)
            if len(kept) >= self.MAX_RECALLED_FRAGMENTS:
                break
        return kept

    def recheck_eligible(self, asset_ids: Iterable[str]) -> set[str]:
        visual_enabled = self._visual_search_enabled()
        ids = sorted({str(asset_id) for asset_id in asset_ids if asset_id})
        if not ids:
            return set()
        statement = (
            select(
                media_library_fragment_index,
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
                    media_library_assets.c.asset_id
                    == media_library_fragment_index.c.asset_id,
                )
                .join(
                    media_library_tasks,
                    media_library_tasks.c.asset_id
                    == media_library_fragment_index.c.asset_id,
                )
                .join(
                    media_library_analysis_runs,
                    media_library_analysis_runs.c.analysis_run_id
                    == media_library_fragment_index.c.analysis_run_id,
                )
            )
            .where(
                media_library_fragment_index.c.asset_id.in_(ids),
                media_library_assets.c.upload_status == "ready",
                media_library_assets.c.archived.is_(False),
                media_library_assets.c.content_sha256.is_not(None),
                self._scheme_eligibility(visual_enabled),
                media_library_fragment_index.c.is_active.is_(True),
                media_library_analysis_runs.c.status == "ready",
                media_library_analysis_runs.c.is_current.is_(True),
                media_library_fragment_index.c.source_version
                == media_library_assets.c.content_sha256,
            )
        )
        with self.engine.connect() as conn:
            return {
                str(row["asset_id"])
                for row in conn.execute(statement).mappings().fetchall()
                if self._row_scheme_eligible(
                    row, visual_enabled=visual_enabled
                )
            }

    @staticmethod
    def _score_fragment(
        row: Mapping[str, Any],
        *,
        original: str,
        exact_phrases: list[str],
        optional_terms: list[str],
        requested_orientation: str,
        dialogue_query: str = "",
        user_query: str = "",
    ) -> tuple[float, list[str]]:
        dialogue = normalize_text(row.get("dialogue_text"))
        summary = normalize_text(row.get("summary"))
        visual_terms = normalized_search_text(
            row.get("visual_labels_json") or [],
            row.get("keywords_json") or [],
        )
        title_and_tags = normalized_search_text(
            row.get("display_name"), row.get("tags_json") or []
        )
        scheme = str(row.get("analysis_scheme") or "")
        score = 0.0
        reasons: list[str] = []
        authoritative_phrases = normalized_unique(
            [user_query, dialogue_query, original]
        )
        if scheme == "dialogue" and any(
            phrase in dialogue for phrase in authoritative_phrases
        ):
            score += 100
            reasons.append("完整原始查询命中对白")
        if scheme == "visual_semantic" and any(
            phrase in summary for phrase in authoritative_phrases
        ):
            score += 90
            reasons.append("视觉摘要完整短语命中")
        visual_query_terms = normalized_unique(
            [*authoritative_phrases, *exact_phrases, *optional_terms]
        )
        if scheme == "visual_semantic" and any(
            term in visual_terms for term in visual_query_terms
        ):
            score += 70
            reasons.append("视觉对象、场景或关键词命中")
        if any(
            phrase in title_and_tags for phrase in authoritative_phrases
        ):
            score += 40
            reasons.append("完整原始查询命中标题或标签")
        semantic_text = (
            dialogue
            if scheme == "dialogue"
            else normalized_search_text(summary, visual_terms)
        )
        exact_hits = sum(
            1 for phrase in exact_phrases if phrase in semantic_text
        )
        if exact_hits:
            score += min(90, exact_hits * 30)
            label = "对白" if scheme == "dialogue" else "视觉"
            reasons.append(f"{label}短语命中 {exact_hits} 项")
        optional_hits = sum(
            1 for term in optional_terms if term in semantic_text
        )
        if optional_hits:
            score += min(40, optional_hits * 8)
            score += 20 * optional_hits / len(optional_terms)
            reasons.append(f"规划关键词命中 {optional_hits}/{len(optional_terms)}")
        title_terms = normalized_unique([*exact_phrases, *optional_terms])
        title_hits = sum(1 for term in title_terms if term in title_and_tags)
        if title_hits:
            score += 10 * title_hits / len(title_terms)
            reasons.append(f"标题或标签词项命中 {title_hits}/{len(title_terms)}")
        if _orientation_matches(
            requested_orientation, row.get("width"), row.get("height")
        ):
            score += 5
            reasons.append("方向完全匹配")
        confidence = row.get("confidence")
        if scheme == "dialogue" and confidence is not None:
            score += max(0.0, min(1.0, float(confidence))) * 5
            reasons.append("对白置信度加分")
        return score, reasons

    def create_search_run(self, values: Mapping[str, Any]) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(media_library_search_runs).values(**dict(values)))

    def update_search_run(self, search_id: str, **values: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(media_library_search_runs)
                .where(media_library_search_runs.c.search_id == search_id)
                .values(**values)
            )

    def get_search_run(self, search_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(
                conn.execute(
                    select(media_library_search_runs).where(
                        media_library_search_runs.c.search_id == search_id
                    )
                ).first()
            )

    def create_action(self, values: Mapping[str, Any]) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(media_library_search_actions).values(**dict(values))
            )

    def list_actions(self, search_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    select(media_library_search_actions)
                    .where(
                        media_library_search_actions.c.search_id == search_id
                    )
                    .order_by(
                        media_library_search_actions.c.created_at,
                        media_library_search_actions.c.id,
                    )
                )
                .mappings()
                .fetchall()
            ]


def _orientation(width: Any, height: Any) -> str:
    if width is None or height is None:
        return "any"
    return "portrait" if int(height) > int(width) else "landscape"


FragmentPublisher = MediaLibraryFragmentPublisher
SearchRepository = MediaLibrarySearchRepository
