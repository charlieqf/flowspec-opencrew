from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from .normalization import normalize_text
from .schemas import MediaLibraryQueryPlanV1, query_is_too_short


PlannerCallable = Callable[
    [dict[str, Any]],
    Mapping[str, Any] | MediaLibraryQueryPlanV1 | Awaitable[Mapping[str, Any] | MediaLibraryQueryPlanV1],
]


@dataclass(frozen=True)
class PlannerOutcome:
    plan: MediaLibraryQueryPlanV1
    degraded: bool
    latency_ms: int
    error_code: str | None = None


class PlannerCallError(RuntimeError):
    """A privacy-safe, structured failure reported by a planner adapter."""

    def __init__(self, code: str) -> None:
        normalized = str(code or "").strip().lower()
        if normalized not in {
            "planner_quota_exceeded",
            "planner_unavailable",
        }:
            normalized = "planner_unavailable"
        self.code = normalized
        super().__init__(normalized)


def deterministic_fallback_plan(
    query: str,
    *,
    orientation: str = "any",
    min_duration_ms: int | None = None,
    max_duration_ms: int | None = None,
    sources: list[str] | None = None,
) -> MediaLibraryQueryPlanV1:
    normalized = normalize_text(query)
    if query_is_too_short(normalized):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "search_query_too_short",
                "user_message": "检索内容至少需要 2 个规范化字符。",
                "suggested_action": "请输入对白原句或更具体的关键词。",
            },
        )
    normalized_parts = [
        part
        for part in dict.fromkeys(
            normalize_text(part) for part in str(query).split()
        )
        if part
    ]
    return MediaLibraryQueryPlanV1(
        original_query=str(query).strip(),
        exact_phrases=list(
            dict.fromkeys([normalized, *normalized_parts])
        )[:20],
        optional_terms=normalized_parts[:40]
        if len(normalized_parts) > 1
        else [],
        negative_terms=[],
        orientation=orientation,
        min_duration_ms=min_duration_ms,
        max_duration_ms=max_duration_ms,
        sources=sources or ["media_library"],
        planner_version="ml_query_planner_v1",
    )


class MediaLibrarySearchPlanner:
    """Optional LLM planner with a deterministic, non-blocking fallback."""

    def __init__(
        self,
        planner: PlannerCallable | None = None,
        *,
        enabled: bool = True,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.planner = planner
        self.enabled = enabled
        self.timeout_seconds = max(0.01, float(timeout_seconds))

    async def plan(
        self,
        query: str,
        *,
        orientation: str = "any",
        min_duration_ms: int | None = None,
        max_duration_ms: int | None = None,
        sources: list[str] | None = None,
        request_context: Mapping[str, Any] | None = None,
    ) -> PlannerOutcome:
        fallback = deterministic_fallback_plan(
            query,
            orientation=orientation,
            min_duration_ms=min_duration_ms,
            max_duration_ms=max_duration_ms,
            sources=sources,
        )
        started = time.monotonic()
        if not self.enabled or self.planner is None:
            return PlannerOutcome(
                plan=fallback,
                degraded=True,
                latency_ms=_elapsed_ms(started),
                error_code="planner_disabled",
            )
        try:
            payload = {
                "schema_version": "media_library_query_plan_request_v1",
                "original_query": str(query).strip(),
                "orientation": orientation,
                "min_duration_ms": min_duration_ms,
                "max_duration_ms": max_duration_ms,
                "sources": sources or ["media_library"],
                "_request_context": {
                    str(key): value
                    for key, value in dict(request_context or {}).items()
                    if str(key) in {"source_asset_id", "target_task_id"}
                },
            }
            if inspect.iscoroutinefunction(self.planner):
                value = await asyncio.wait_for(
                    self.planner(payload), timeout=self.timeout_seconds
                )
            else:
                value = await asyncio.wait_for(
                    asyncio.to_thread(self.planner, payload),
                    timeout=self.timeout_seconds,
                )
                if inspect.isawaitable(value):
                    value = await asyncio.wait_for(
                        value, timeout=self.timeout_seconds
                    )
            plan = (
                value
                if isinstance(value, MediaLibraryQueryPlanV1)
                else MediaLibraryQueryPlanV1.model_validate(value)
            )
            # Explicit request filters remain authoritative.
            plan = plan.model_copy(
                update={
                    "original_query": str(query).strip(),
                    "orientation": orientation,
                    "min_duration_ms": min_duration_ms,
                    "max_duration_ms": max_duration_ms,
                    "sources": sources or ["media_library"],
                }
            )
            if not plan.exact_phrases and not plan.optional_terms:
                plan = plan.model_copy(
                    update={"exact_phrases": [normalize_text(query)]}
                )
            return PlannerOutcome(
                plan=plan, degraded=False, latency_ms=_elapsed_ms(started)
            )
        except asyncio.TimeoutError:
            error_code = "planner_timeout"
        except PlannerCallError as exc:
            error_code = exc.code
        except (ValidationError, ValueError, TypeError, KeyError):
            error_code = "planner_invalid"
        except Exception:
            # Provider outages, quota failures and adapter exceptions all degrade
            # to the same safe deterministic retrieval path.
            error_code = "planner_unavailable"
        return PlannerOutcome(
            plan=fallback,
            degraded=True,
            latency_ms=_elapsed_ms(started),
            error_code=error_code,
        )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))
