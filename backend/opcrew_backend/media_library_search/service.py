from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.engine import Engine

from ..media_library_features import require_media_library_feature
from .planner import MediaLibrarySearchPlanner, PlannerOutcome
from .repository import MediaLibrarySearchRepository
from .schemas import (
    MatchedMediaLibraryFragment,
    MediaLibrarySearchAction,
    MediaLibrarySearchCandidate,
    MediaLibrarySearchRequest,
    MediaLibrarySearchResponse,
)
from .telemetry import SearchTelemetry


RETRIEVAL_VERSION = "dialogue_visual_literal_v1"


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def new_search_id(timestamp_ms: int) -> str:
    return f"mls_{timestamp_ms:013d}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class StartedSearchRun:
    search_id: str
    request: MediaLibrarySearchRequest
    outcome: PlannerOutcome
    started_monotonic: float


class MediaLibrarySearchService:
    def __init__(
        self,
        engine: Engine | None = None,
        *,
        repository: MediaLibrarySearchRepository | None = None,
        planner: MediaLibrarySearchPlanner | None = None,
        telemetry: SearchTelemetry | None = None,
        capacity_sample_interval_seconds: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if repository is None:
            if engine is None:
                raise ValueError("search_repository_or_engine_required")
            repository = MediaLibrarySearchRepository(engine)
        self.repository = repository
        self.planner = planner or MediaLibrarySearchPlanner(enabled=False)
        self.telemetry = telemetry or SearchTelemetry(repository)
        self._capacity_sample_interval_seconds = max(
            0.0, float(capacity_sample_interval_seconds)
        )
        self._capacity_monotonic = monotonic
        self._capacity_last_sampled_at: float | None = None
        self._capacity_sample_lock = asyncio.Lock()

    async def _observe_capacity_if_due(self) -> None:
        capacity_reader = getattr(self.repository, "capacity", None)
        if not callable(capacity_reader):
            return
        now = self._capacity_monotonic()
        if (
            self._capacity_last_sampled_at is not None
            and now - self._capacity_last_sampled_at
            < self._capacity_sample_interval_seconds
        ):
            return
        async with self._capacity_sample_lock:
            now = self._capacity_monotonic()
            if (
                self._capacity_last_sampled_at is not None
                and now - self._capacity_last_sampled_at
                < self._capacity_sample_interval_seconds
            ):
                return
            capacity = await asyncio.to_thread(capacity_reader)
            self._capacity_last_sampled_at = self._capacity_monotonic()
            observer = self.telemetry.observe_capacity
            values = {
                "ready_assets": capacity["ready_assets"],
                "active_dialogue_fragments": capacity[
                    "active_dialogue_fragments"
                ],
                "active_visual_fragments": capacity.get(
                    "active_visual_fragments", 0
                ),
                "search_eligible_clips": capacity.get(
                    "search_eligible_clips", 0
                ),
            }
            parameters = inspect.signature(observer).parameters
            accepts_all = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
            observer(
                **(
                    values
                    if accepts_all
                    else {
                        key: value
                        for key, value in values.items()
                        if key in parameters
                    }
                )
            )

    async def plan(self, request: MediaLibrarySearchRequest | Mapping[str, Any]) -> PlannerOutcome:
        require_media_library_feature("library_search")
        payload = self._request(request)
        return await self.planner.plan(
            payload.query,
            orientation=payload.orientation,
            min_duration_ms=payload.min_duration_ms,
            max_duration_ms=payload.max_duration_ms,
            sources=payload.sources,
            request_context={
                "target_task_id": payload.target_task_id,
                "source_asset_id": payload.source_asset_id,
            },
        )

    async def search(self, request: MediaLibrarySearchRequest | Mapping[str, Any]) -> MediaLibrarySearchResponse:
        started = await self.begin_search(request)
        return await self.retrieve_started(started)

    async def begin_search(self, request: MediaLibrarySearchRequest | Mapping[str, Any]) -> StartedSearchRun:
        require_media_library_feature("library_search")
        payload = self._request(request)
        total_started = time.monotonic()
        timestamp = int(time.time() * 1000)
        await self._observe_capacity_if_due()
        outcome = await self.plan(payload)
        search_id = new_search_id(timestamp)
        if outcome.degraded:
            self.telemetry.planner_degraded(outcome.error_code)
        self.telemetry.create_run(
            search_id=search_id,
            request=payload,
            plan=outcome.plan,
            planner_degraded=outcome.degraded,
            planner_error_code=outcome.error_code,
            planner_latency_ms=outcome.latency_ms,
            timestamp=timestamp,
        )
        return StartedSearchRun(
            search_id=search_id,
            request=payload,
            outcome=outcome,
            started_monotonic=total_started,
        )

    async def retrieve_started(self, started: StartedSearchRun) -> MediaLibrarySearchResponse:
        require_media_library_feature("library_search")
        payload = started.request
        outcome = started.outcome
        search_id = started.search_id
        try:
            retrieval_started = time.monotonic()
            rows = await asyncio.to_thread(
                self.repository.retrieve,
                outcome.plan,
                exclude_asset_id=payload.source_asset_id,
                dialogue_query=payload.dialogue_query,
                user_query=payload.user_query,
            )
            candidates = self._aggregate(rows)
            clip_rows: list[dict[str, Any]] = []
            clip_retriever = getattr(
                self.repository, "retrieve_clips", None
            )
            if callable(clip_retriever):
                clip_rows = await asyncio.to_thread(
                    clip_retriever,
                    outcome.plan,
                    exclude_asset_id=payload.source_asset_id,
                    dialogue_query=payload.dialogue_query,
                    user_query=payload.user_query,
                )
            candidates.extend(self._clip_candidates(clip_rows))
            eligible = await asyncio.to_thread(
                self.repository.recheck_eligible,
                [
                    candidate.source_asset_id
                    for candidate in candidates
                    if candidate.candidate_kind == "original_video"
                ],
            )
            clip_eligible: set[str] = set()
            clip_rechecker = getattr(
                self.repository, "recheck_clip_eligible", None
            )
            if callable(clip_rechecker):
                clip_eligible = await asyncio.to_thread(
                    clip_rechecker,
                    [
                        candidate.candidate_id
                        for candidate in candidates
                        if candidate.candidate_kind == "derived_clip"
                    ],
                )
            candidates = [
                candidate
                for candidate in candidates
                if (
                    candidate.candidate_kind == "original_video"
                    and candidate.source_asset_id in eligible
                )
                or (
                    candidate.candidate_kind == "derived_clip"
                    and candidate.candidate_id in clip_eligible
                )
            ]
            updated_at = {
                ("original_video", str(row["asset_id"])): int(
                    row.get("asset_updated_at") or 0
                )
                for row in rows
            }
            updated_at.update(
                {
                    ("derived_clip", str(row["clip_id"])): int(
                        row.get("candidate_updated_at") or 0
                    )
                    for row in clip_rows
                }
            )
            candidates.sort(
                key=lambda candidate: (
                    -candidate.raw_score,
                    -updated_at.get(
                        (candidate.candidate_kind, candidate.candidate_id), 0
                    ),
                    candidate.candidate_kind,
                    candidate.candidate_id,
                )
            )
            retrieval_latency_ms = _elapsed_ms(retrieval_started)
            total_count = len(candidates)
            page = candidates[payload.offset : payload.offset + payload.limit]
            total_latency_ms = _elapsed_ms(started.started_monotonic)
            finished_at = int(time.time() * 1000)
            self.telemetry.complete_run(
                search_id=search_id,
                candidates=candidates[:50],
                result_count=total_count,
                retrieval_latency_ms=retrieval_latency_ms,
                total_latency_ms=total_latency_ms,
                timestamp=finished_at,
            )
            return MediaLibrarySearchResponse(
                search_id=search_id,
                retrieval_version=RETRIEVAL_VERSION,
                planner_version=outcome.plan.planner_version,
                planner_degraded=outcome.degraded,
                result_count=len(page),
                total_count=total_count,
                limit=payload.limit,
                offset=payload.offset,
                items=page,
            )
        except Exception:
            self.telemetry.fail_run(
                search_id=search_id,
                error_code="search_retrieval_failed",
                total_latency_ms=_elapsed_ms(started.started_monotonic),
                timestamp=int(time.time() * 1000),
            )
            raise

    def complete_editor_run(
        self,
        started: StartedSearchRun,
        *,
        candidates: list[Mapping[str, Any]],
        source_runs: Mapping[str, str | None],
        source_errors: Mapping[str, Mapping[str, Any]],
    ) -> bool:
        elapsed = _elapsed_ms(started.started_monotonic)
        return self.telemetry.complete_editor_run(
            search_id=started.search_id,
            candidates=candidates,
            source_runs=source_runs,
            source_errors=source_errors,
            retrieval_latency_ms=elapsed,
            total_latency_ms=elapsed,
            timestamp=int(time.time() * 1000),
        )

    def fail_editor_run(
        self,
        started: StartedSearchRun,
        *,
        source_runs: Mapping[str, str | None],
        source_errors: Mapping[str, Mapping[str, Any]],
    ) -> bool:
        return self.telemetry.fail_editor_run(
            search_id=started.search_id,
            source_runs=source_runs,
            source_errors=source_errors,
            total_latency_ms=_elapsed_ms(started.started_monotonic),
            timestamp=int(time.time() * 1000),
        )

    def search_sync(self, request: MediaLibrarySearchRequest | Mapping[str, Any]) -> MediaLibrarySearchResponse:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.search(request))
        raise RuntimeError("search_sync_cannot_run_inside_event_loop")

    def get_run(self, search_id: str) -> dict[str, Any]:
        row = self.repository.get_search_run(search_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "search_run_not_found",
                    "user_message": "检索运行不存在或已清理。",
                },
            )
        plan = dict(row.get("query_plan_json") or {})
        plan.pop("retained_original_query", None)
        row["query_plan_json"] = plan
        return row

    def record_action(
        self,
        action: MediaLibrarySearchAction | Mapping[str, Any],
        *,
        timestamp: int | None = None,
    ) -> bool:
        require_media_library_feature("library_search")
        payload = action if isinstance(action, MediaLibrarySearchAction) else MediaLibrarySearchAction.model_validate(action)
        return self.telemetry.record_action(
            payload,
            timestamp=timestamp if timestamp is not None else int(time.time() * 1000),
        )

    @staticmethod
    def _request(request: MediaLibrarySearchRequest | Mapping[str, Any]) -> MediaLibrarySearchRequest:
        if isinstance(request, MediaLibrarySearchRequest):
            return request
        try:
            return MediaLibrarySearchRequest.model_validate(request)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "search_request_invalid",
                    "user_message": "检索请求格式不正确。",
                },
            ) from exc

    @staticmethod
    def _aggregate(rows: list[dict[str, Any]]) -> list[MediaLibrarySearchCandidate]:
        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row["asset_id"])].append(row)
        candidates: list[tuple[MediaLibrarySearchCandidate, int]] = []
        for asset_id, fragments in grouped.items():
            fragments.sort(
                key=lambda row: (
                    -float(row["_raw_score"]),
                    int(row["start_ms"]),
                    str(row["fragment_id"]),
                )
            )
            fragments = fragments[:3]
            weights = (1.0, 0.15, 0.05)
            aggregate_score = sum(float(fragment["_raw_score"]) * weights[index] for index, fragment in enumerate(fragments))
            reasons: list[str] = []
            for fragment in fragments:
                for reason in fragment["_score_reasons"]:
                    if reason not in reasons:
                        reasons.append(reason)
            first = fragments[0]
            width, height = first.get("width"), first.get("height")
            orientation = "any" if width is None or height is None else "portrait" if int(height) > int(width) else "landscape"
            matched = [
                MatchedMediaLibraryFragment(
                    scheme=str(fragment["analysis_scheme"]),
                    analysis_scheme=str(fragment["analysis_scheme"]),
                    run_id=str(fragment["analysis_run_id"]),
                    fragment_id=str(fragment["fragment_id"]),
                    start_ms=int(fragment["start_ms"]),
                    end_ms=int(fragment["end_ms"]),
                    dialogue_text=fragment.get("dialogue_text"),
                    summary=fragment.get("summary"),
                    keyframe_ref=fragment.get("keyframe_ref_json"),
                    raw_score=float(fragment["_raw_score"]),
                    score_reasons=list(fragment["_score_reasons"]),
                )
                for fragment in fragments
            ]
            candidates.append(
                (
                    MediaLibrarySearchCandidate(
                        candidate_kind="original_video",
                        candidate_id=asset_id,
                        asset_id=asset_id,
                        source_asset_id=asset_id,
                        source_clip_id=None,
                        source_version=str(first["source_version"]),
                        content_sha256=str(first["source_version"]),
                        display_name=str(first["display_name"]),
                        thumbnail_url=first.get("thumbnail_url"),
                        preview_url=first.get("preview_url"),
                        duration_ms=(int(first["duration_ms"]) if first.get("duration_ms") is not None else None),
                        orientation=orientation,
                        score=min(1.0, aggregate_score / 200),
                        raw_score=aggregate_score,
                        score_reasons=reasons,
                        matched_fragments=matched,
                    ),
                    int(first.get("asset_updated_at") or 0),
                )
            )
        candidates.sort(
            key=lambda item: (
                -item[0].raw_score,
                -item[1],
                item[0].candidate_kind,
                item[0].candidate_id,
            )
        )
        return [candidate for candidate, _ in candidates]

    @staticmethod
    def _clip_candidates(
        rows: list[dict[str, Any]],
    ) -> list[MediaLibrarySearchCandidate]:
        candidates: list[MediaLibrarySearchCandidate] = []
        for row in rows:
            width, height = row.get("width"), row.get("height")
            orientation = (
                "any"
                if width is None or height is None
                else "portrait"
                if int(height) > int(width)
                else "landscape"
            )
            path = str(row.get("output_path") or "")
            encoded_path = "/".join(
                quote(part, safe="") for part in path.split("/")
            )
            session_id = int(row.get("source_session_id") or 0)
            preview_url = (
                f"/api/session-tasks/{session_id}/raw/{encoded_path}"
                if session_id > 0 and encoded_path
                else None
            )
            duration_ms = int(row.get("duration_ms") or 0)
            candidates.append(
                MediaLibrarySearchCandidate(
                    candidate_kind="derived_clip",
                    candidate_id=str(row["clip_id"]),
                    asset_id=None,
                    source_asset_id=str(row["source_asset_id"]),
                    source_clip_id=str(row["clip_id"]),
                    source_version=str(row["source_version"]),
                    content_sha256=str(row["content_sha256"]),
                    display_name=str(row["display_name"]),
                    preview_url=preview_url,
                    thumbnail_url=None,
                    duration_ms=duration_ms,
                    tags=list(row.get("tags_json") or []),
                    candidate_start_ms=0,
                    candidate_end_ms=duration_ms,
                    source_start_ms=int(row["source_start_ms"]),
                    source_end_ms=int(row["source_end_ms"]),
                    time_basis="candidate",
                    orientation=orientation,
                    score=min(1.0, float(row["_raw_score"]) / 250),
                    raw_score=float(row["_raw_score"]),
                    score_reasons=list(row["_score_reasons"]),
                    matched_fragments=[],
                    allowed_actions=["preview", "import_clip"],
                )
            )
        return candidates


SearchService = MediaLibrarySearchService
