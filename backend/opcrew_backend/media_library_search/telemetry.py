from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import PurePath
from typing import Any

from .normalization import NORMALIZATION_VERSION, normalize_text, query_hash
from .repository import MediaLibrarySearchRepository
from .schemas import (
    MediaLibraryQueryPlanV1,
    MediaLibrarySearchAction,
    MediaLibrarySearchCandidate,
    MediaLibrarySearchRequest,
)


LOGGER = logging.getLogger(__name__)
MetricSink = Callable[[str, int], None]
EventSink = Callable[[str, dict[str, Any]], None]
_SENSITIVE_KEYS = {
    "absolute_path",
    "api_key",
    "dialogue",
    "dialogue_text",
    "download_url",
    "file_path",
    "original_query",
    "path",
    "preview_url",
    "prompt",
    "query",
    "raw_query",
    "secret",
    "system_prompt",
    "thumbnail_url",
    "token",
    "url",
    "workspace_dir",
}
_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_SAFE_ERROR_CODE_RE = re.compile(r"^[a-z0-9_:-]{1,128}$")


def _hash_term(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def privacy_safe_query_plan(
    plan: MediaLibraryQueryPlanV1,
    *,
    planner_degraded: bool,
    retain_raw_query: bool = False,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": plan.schema_version,
        "normalization_version": NORMALIZATION_VERSION,
        "query_hash": query_hash(plan.original_query),
        "exact_phrase_count": len(plan.exact_phrases),
        "optional_term_count": len(plan.optional_terms),
        "negative_term_count": len(plan.negative_terms),
        "exact_phrase_hashes": [_hash_term(item) for item in plan.exact_phrases],
        "optional_term_hashes": [_hash_term(item) for item in plan.optional_terms],
        "negative_term_hashes": [_hash_term(item) for item in plan.negative_terms],
        "orientation": plan.orientation,
        "min_duration_ms": plan.min_duration_ms,
        "max_duration_ms": plan.max_duration_ms,
        "sources": list(plan.sources),
        "planner_version": plan.planner_version,
        "planner_degraded": planner_degraded,
        "tokenizer_name": "none",
        "tokenizer_version": "none",
        "reranker_version": "none",
    }
    if retain_raw_query:
        value["retained_original_query"] = plan.original_query
    return value


def privacy_safe_candidates(
    candidates: list[MediaLibrarySearchCandidate],
) -> list[dict[str, Any]]:
    return [
        {
            "source": candidate.source,
            "candidate_kind": candidate.candidate_kind,
            "candidate_id": candidate.candidate_id,
            "source_asset_id": candidate.source_asset_id,
            "source_clip_id": candidate.source_clip_id,
            "source_version": candidate.source_version,
            "content_sha256": candidate.content_sha256,
            "rank": rank,
            "score": candidate.score,
            "matched_fragment_ids": [fragment.fragment_id for fragment in candidate.matched_fragments],
        }
        for rank, candidate in enumerate(candidates, start=1)
    ]


def privacy_safe_editor_candidates(
    candidates: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for rank, candidate in enumerate(candidates, start=1):
        source = str(candidate.get("source") or "")
        candidate_id = _safe_snapshot_identifier(candidate.get("candidate_id"))
        if source not in {"media_library", "external"} or not candidate_id:
            continue
        fragments = candidate.get("matched_fragments")
        fragment_ids = [
            fragment_id
            for item in (fragments if isinstance(fragments, list) else [])
            if isinstance(item, Mapping) and (fragment_id := _safe_snapshot_identifier(item.get("fragment_id")))
        ]
        candidate_kind = str(candidate.get("candidate_kind") or "")
        if source == "media_library" and candidate_kind not in {
            "original_video",
            "derived_clip",
        }:
            continue
        source_asset_id = _safe_snapshot_identifier(
            candidate.get("source_asset_id") or candidate.get("asset_id")
        ) if source == "media_library" else None
        source_clip_id = (
            _safe_snapshot_identifier(candidate.get("source_clip_id"))
            if source == "media_library"
            and candidate_kind == "derived_clip"
            else None
        )
        if (
            source == "media_library"
            and candidate_kind == "derived_clip"
            and source_clip_id != candidate_id
        ):
            continue
        source_version = str(candidate.get("source_version") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", source_version):
            source_version = ""
        try:
            score = float(candidate.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        if not math.isfinite(score):
            score = 0.0
        snapshots.append(
            {
                "source": source,
                "candidate_kind": (
                    candidate_kind if source == "media_library" else None
                ),
                "candidate_id": candidate_id,
                "source_asset_id": source_asset_id,
                "source_clip_id": source_clip_id,
                "source_version": (source_version or None if source == "media_library" else None),
                "content_sha256": (
                    str(candidate.get("content_sha256") or source_version)
                    if source == "media_library"
                    else None
                ),
                "rank": rank,
                "score": score,
                "matched_fragment_ids": fragment_ids,
            }
        )
    return snapshots


def _safe_snapshot_identifier(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 512 or "://" in candidate or candidate.startswith(("/", "\\", "../", "..\\")) or "/" in candidate or "\\" in candidate:
        return None
    return candidate


def privacy_safe_source_runs(
    source_runs: Mapping[str, str | None],
    source_errors: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for source in ("media_library", "external"):
        if source not in source_runs:
            continue
        run_id = str(source_runs.get(source) or "")
        value[source] = run_id if _SAFE_RUN_ID_RE.fullmatch(run_id) else None
    errors: dict[str, dict[str, str]] = {}
    for source, error in source_errors.items():
        if source not in {"media_library", "external"}:
            continue
        code = str(error.get("code") or f"{source}_search_failed").lower()
        if not _SAFE_ERROR_CODE_RE.fullmatch(code):
            code = f"{source}_search_failed"
        errors[source] = {"code": code}
    if errors:
        value["source_errors"] = errors
    return value


def sanitize_action_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): sanitized for key, item in value.items() if str(key).casefold() not in _SENSITIVE_KEYS and (sanitized := _sanitize_value(item)) is not None
    }


def _sanitize_value(value: Any) -> Any | None:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        path = PurePath(candidate.replace("\\", "/"))
        if "://" in candidate or candidate.startswith("/") or candidate.startswith("../") or ".." in path.parts:
            return None
        return candidate[:512]
    if isinstance(value, list):
        return [sanitized for item in value[:100] if (sanitized := _sanitize_value(item)) is not None]
    if isinstance(value, Mapping):
        return sanitize_action_metadata(value)
    return None


class SearchTelemetry:
    """Best-effort persistence; telemetry faults never fail search/import."""

    def __init__(
        self,
        repository: MediaLibrarySearchRepository,
        *,
        metric_sink: MetricSink | None = None,
        event_sink: EventSink | None = None,
        raw_query_retention_days: int | None = None,
    ) -> None:
        self.repository = repository
        self.metric_sink = metric_sink
        self.event_sink = event_sink
        if raw_query_retention_days is None:
            raw_query_retention_days = _retention_days_from_environment()
        self.raw_query_retention_days = max(0, int(raw_query_retention_days))
        self._rolling_total_latency_ms: deque[int] = deque(maxlen=200)

    def observe_capacity(
        self,
        *,
        ready_assets: int,
        active_dialogue_fragments: int,
        active_visual_fragments: int = 0,
        search_eligible_clips: int = 0,
    ) -> None:
        self._metric("media_library_ready_assets", ready_assets)
        self._metric(
            "media_library_active_dialogue_fragments",
            active_dialogue_fragments,
        )
        self._metric(
            "media_library_active_visual_fragments",
            active_visual_fragments,
        )
        self._metric(
            "media_library_search_eligible_clips",
            search_eligible_clips,
        )
        if ready_assets >= 500:
            LOGGER.warning(
                "media_library_capacity_boundary_exceeded "
                "ready_assets=%d committed_boundary=500",
                ready_assets,
            )
        elif ready_assets >= 450:
            LOGGER.warning(
                "media_library_capacity_warning "
                "ready_assets=%d warning_boundary=450",
                ready_assets,
            )

    def create_run(
        self,
        *,
        search_id: str,
        request: MediaLibrarySearchRequest,
        plan: MediaLibraryQueryPlanV1,
        planner_degraded: bool,
        planner_latency_ms: int,
        timestamp: int,
        planner_error_code: str | None = None,
    ) -> bool:
        query_plan = privacy_safe_query_plan(
            plan,
            planner_degraded=planner_degraded,
            retain_raw_query=self.raw_query_retention_days > 0,
        )
        query_plan["sources"] = list(request.sources)
        if planner_degraded and _SAFE_ERROR_CODE_RE.fullmatch(
            str(planner_error_code or "")
        ):
            query_plan["planner_error_code"] = str(planner_error_code)
        return self._best_effort(
            "create_run",
            self.repository.create_search_run,
            {
                "search_id": search_id,
                "entry_point": request.entry_point,
                "target_task_id": request.target_task_id,
                "dialogue_asset_key": request.dialogue_asset_key,
                "source_asset_id": request.source_asset_id,
                "query_source": request.query_source,
                "query_hash": query_hash(request.query),
                "query_plan_json": query_plan,
                "planner_version": plan.planner_version,
                "retrieval_version": "dialogue_visual_literal_v1",
                "planner_degraded": planner_degraded,
                "requested_sources_json": list(request.sources),
                "source_runs_json": {},
                "status": "running",
                "result_count": 0,
                "zero_result": True,
                "planner_latency_ms": planner_latency_ms,
                "retrieval_latency_ms": None,
                "total_latency_ms": None,
                "top_candidates_json": [],
                "error_code": None,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )

    def complete_run(
        self,
        *,
        search_id: str,
        candidates: list[MediaLibrarySearchCandidate],
        result_count: int,
        retrieval_latency_ms: int,
        total_latency_ms: int,
        timestamp: int,
    ) -> bool:
        success = self._best_effort(
            "complete_run",
            self.repository.update_search_run,
            search_id,
            status="completed",
            result_count=result_count,
            zero_result=result_count == 0,
            retrieval_latency_ms=retrieval_latency_ms,
            total_latency_ms=total_latency_ms,
            top_candidates_json=privacy_safe_candidates(candidates),
            updated_at=timestamp,
        )
        self._metric("media_library_search_total", 1)
        if result_count == 0:
            self._metric("media_library_search_zero_result_total", 1)
        self._metric("media_library_search_latency_ms", total_latency_ms)
        self._metric("media_library_search_retrieval_latency_ms", retrieval_latency_ms)
        for candidate in candidates:
            self._metric(
                "media_library_search_candidate_"
                f"{candidate.candidate_kind}_total",
                1,
            )
            if candidate.candidate_kind == "derived_clip":
                self._metric(
                    "media_library_search_match_clip_metadata_total", 1
                )
            for fragment in candidate.matched_fragments:
                self._metric(
                    "media_library_search_match_"
                    f"{fragment.analysis_scheme}_total",
                    1,
                )
        self._observe_search_latency(total_latency_ms)
        self._event(
            "media_library.search.completed",
            {
                "search_id": search_id,
                "result_count": result_count,
                "retrieval_latency_ms": retrieval_latency_ms,
                "total_latency_ms": total_latency_ms,
            },
        )
        return success

    def fail_run(
        self,
        *,
        search_id: str,
        error_code: str,
        total_latency_ms: int,
        timestamp: int,
    ) -> bool:
        success = self._best_effort(
            "fail_run",
            self.repository.update_search_run,
            search_id,
            status="failed",
            error_code=error_code,
            total_latency_ms=total_latency_ms,
            updated_at=timestamp,
        )
        self._event(
            "media_library.search.failed",
            {
                "search_id": search_id,
                "error_code": error_code,
                "total_latency_ms": total_latency_ms,
            },
        )
        return success

    def complete_editor_run(
        self,
        *,
        search_id: str,
        candidates: list[Mapping[str, Any]],
        source_runs: Mapping[str, str | None],
        source_errors: Mapping[str, Mapping[str, Any]],
        retrieval_latency_ms: int,
        total_latency_ms: int,
        timestamp: int,
    ) -> bool:
        snapshots = privacy_safe_editor_candidates(candidates)
        success = self._best_effort(
            "complete_editor_run",
            self.repository.update_search_run,
            search_id,
            status="completed",
            source_runs_json=privacy_safe_source_runs(source_runs, source_errors),
            result_count=len(snapshots),
            zero_result=len(snapshots) == 0,
            retrieval_latency_ms=retrieval_latency_ms,
            total_latency_ms=total_latency_ms,
            top_candidates_json=snapshots,
            error_code=("partial_source_failure" if source_errors else None),
            updated_at=timestamp,
        )
        self._metric("media_library_search_total", 1)
        if not snapshots:
            self._metric("media_library_search_zero_result_total", 1)
        self._metric("media_library_search_latency_ms", total_latency_ms)
        self._metric(
            "media_library_search_retrieval_latency_ms",
            retrieval_latency_ms,
        )
        self._observe_search_latency(total_latency_ms)
        self._event(
            "media_library.search.completed",
            {
                "search_id": search_id,
                "result_count": len(snapshots),
                "retrieval_latency_ms": retrieval_latency_ms,
                "total_latency_ms": total_latency_ms,
                "partial_source_failure": bool(source_errors),
            },
        )
        return success

    def fail_editor_run(
        self,
        *,
        search_id: str,
        source_runs: Mapping[str, str | None],
        source_errors: Mapping[str, Mapping[str, Any]],
        total_latency_ms: int,
        timestamp: int,
    ) -> bool:
        success = self._best_effort(
            "fail_editor_run",
            self.repository.update_search_run,
            search_id,
            status="failed",
            source_runs_json=privacy_safe_source_runs(source_runs, source_errors),
            result_count=0,
            zero_result=True,
            total_latency_ms=total_latency_ms,
            top_candidates_json=[],
            error_code="editor_search_failed",
            updated_at=timestamp,
        )
        self._event(
            "media_library.search.failed",
            {
                "search_id": search_id,
                "error_code": "editor_search_failed",
                "total_latency_ms": total_latency_ms,
            },
        )
        return success

    def record_action(
        self,
        action: MediaLibrarySearchAction,
        *,
        timestamp: int,
    ) -> bool:
        try:
            run = self.repository.get_search_run(action.search_id)
        except Exception as exc:
            LOGGER.warning(
                "media_library_search_telemetry_failed operation=resolve_action_rank error_type=%s",
                type(exc).__name__,
            )
            self._metric("media_library_search_telemetry_failure_total", 1)
            return False
        if run is None:
            return False
        matched = next(
            (
                item
                for item in list(run.get("top_candidates_json") or [])
                if str(item.get("source")) == action.source and str(item.get("candidate_id")) == action.candidate_id
            ),
            None,
        )
        if matched is None:
            return False
        action = action.model_copy(
            update={
                "candidate_rank": int(matched["rank"]),
                "source_asset_id": (str(matched.get("source_asset_id") or "") or None if action.source == "media_library" else None),
            }
        )
        success = self._best_effort(
            "record_action",
            self.repository.create_action,
            {
                "search_id": action.search_id,
                "action_kind": action.action_kind,
                "source": action.source,
                "candidate_id": action.candidate_id,
                "source_asset_id": action.source_asset_id,
                "candidate_rank": action.candidate_rank,
                "target_task_id": action.target_task_id,
                "metadata_json": sanitize_action_metadata(action.metadata),
                "created_at": timestamp,
            },
        )
        if success:
            self._metric("media_library_search_action_total", 1)
            self._event(
                "media_library.search.action",
                {
                    "search_id": action.search_id,
                    "action_kind": action.action_kind,
                    "source": action.source,
                    "candidate_id": action.candidate_id,
                    "candidate_rank": action.candidate_rank,
                    "target_task_id": action.target_task_id,
                },
            )
        return success

    def planner_degraded(self, error_code: str | None = None) -> None:
        self._metric("media_library_search_planner_degraded_total", 1)
        safe_code = str(error_code or "")
        self._event(
            "media_library.search.planner_degraded",
            {
                "error_code": (
                    safe_code
                    if _SAFE_ERROR_CODE_RE.fullmatch(safe_code)
                    else "planner_unavailable"
                )
            },
        )

    def _observe_search_latency(self, value: int) -> None:
        self._rolling_total_latency_ms.append(max(0, int(value)))
        if len(self._rolling_total_latency_ms) < 20:
            return
        ordered = sorted(self._rolling_total_latency_ms)
        p95_index = max(
            0,
            math.ceil(len(ordered) * 0.95) - 1,
        )
        p95_ms = ordered[p95_index]
        self._metric("media_library_search_rolling_p95_ms", p95_ms)
        if p95_ms > 3000:
            LOGGER.warning(
                "media_library_search_latency_p95_warning "
                "sample_count=%d p95_ms=%d threshold_ms=3000",
                len(ordered),
                p95_ms,
            )

    def _best_effort(self, operation: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
        try:
            callback(*args, **kwargs)
            return True
        except Exception as exc:
            LOGGER.warning(
                "media_library_search_telemetry_failed operation=%s error_type=%s",
                operation,
                type(exc).__name__,
            )
            self._metric("media_library_search_telemetry_failure_total", 1)
            return False

    def _metric(self, name: str, value: int) -> None:
        if self.metric_sink is None:
            return
        try:
            self.metric_sink(name, int(value))
        except Exception:
            LOGGER.warning("media_library_search_metric_sink_failed metric=%s", name)

    def _event(self, kind: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(kind, payload)
        except Exception:
            LOGGER.warning(
                "media_library_search_event_sink_failed kind=%s",
                kind,
            )


def _retention_days_from_environment() -> int:
    raw = os.getenv("OPENCREW_MEDIA_SEARCH_RAW_QUERY_RETENTION_DAYS", "0")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


MediaLibrarySearchTelemetry = SearchTelemetry
