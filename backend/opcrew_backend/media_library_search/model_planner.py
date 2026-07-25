from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError

from fastapi import HTTPException

from ..media_library_imports.repository import MediaLibraryImportRepository
from ..model_policy import (
    SURFACE_MEDIA_LIBRARY_SEARCH_PLANNER,
    resolve_prompt_model_for_role,
    surface_policy,
)
from ..routes.auth import AUTH_ROLE_USER
from ..services.opencode_runtime import opencode_client_for_context
from .planner import PlannerCallError


PLANNER_VERSION = "ml_query_planner_opencode_v1"
SYSTEM_PROMPT = """Expand one media-library search query conservatively.
Return one strict JSON object and no markdown. The only allowed keys are
exact_phrases, optional_terms, and negative_terms. Each value must be an array
of short strings. Preserve the source language. Prefer precise phrases and
close synonyms; do not invent names, brands, sensitive traits, or visual facts.
The server owns the original query, source filters, orientation, and duration
filters, so do not return or modify them."""
DISABLED_MODEL_TOOLS = {
    name: False
    for name in (
        "bash",
        "edit",
        "glob",
        "grep",
        "list",
        "read",
        "skill",
        "task",
        "todowrite",
        "webfetch",
        "websearch",
        "write",
    )
}
MODEL_OUTPUT_FIELDS = {
    "exact_phrases",
    "optional_terms",
    "negative_terms",
}


def _environment_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _environment_float(
    name: str, default: float, *, minimum: float
) -> float:
    try:
        return max(minimum, float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _model_catalog(client: Any) -> dict[str, Any]:
    payload = client.providers(timeout=2)
    connected = {
        str(item)
        for item in (payload.get("connected") or [])
        if str(item).strip()
    }
    defaults = payload.get("default") or {}
    items: list[dict[str, Any]] = []
    default_model = {"providerID": "", "modelID": ""}
    for provider in payload.get("all") or []:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id") or "").strip()
        if not provider_id or provider_id not in connected:
            continue
        models = provider.get("models") or {}
        if not isinstance(models, dict):
            continue
        for raw in models.values():
            model = raw if isinstance(raw, dict) else {}
            model_id = str(model.get("id") or "").strip()
            if not model_id:
                continue
            items.append(
                {
                    "providerID": provider_id,
                    "providerName": str(
                        provider.get("name") or provider_id
                    ),
                    "modelID": model_id,
                    "modelName": str(model.get("name") or model_id),
                    "inputModalities": list(
                        ((model.get("modalities") or {}).get("input") or [])
                    ),
                }
            )
        configured = str(defaults.get(provider_id) or "").strip()
        if configured and not default_model["providerID"]:
            default_model = {
                "providerID": provider_id,
                "modelID": configured,
            }
    if not default_model["providerID"] and items:
        default_model = {
            "providerID": str(items[0]["providerID"]),
            "modelID": str(items[0]["modelID"]),
        }
    return {"items": items, "default_model": default_model}


def _last_completed_assistant(
    messages: list[dict[str, Any]], started_after: int
) -> str | None:
    for message in reversed(messages):
        info = message.get("info") or {}
        if info.get("role") != "assistant":
            continue
        completed = int(
            ((info.get("time") or {}).get("completed") or 0) or 0
        )
        if completed < started_after:
            continue
        text = "\n".join(
            str(part.get("text") or "").strip()
            for part in (message.get("parts") or [])
            if isinstance(part, dict)
            and part.get("type") == "text"
            and str(part.get("text") or "").strip()
        ).strip()
        if text:
            return text
    return None


def _strict_model_output(
    assistant_text: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    parsed = json.loads(str(assistant_text or "").strip())
    if not isinstance(parsed, dict):
        raise ValueError("search_planner_model_json_invalid")
    unknown = set(parsed) - MODEL_OUTPUT_FIELDS
    if unknown:
        raise ValueError("search_planner_model_unknown_field")
    for field in MODEL_OUTPUT_FIELDS:
        value = parsed.get(field, [])
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError("search_planner_model_terms_invalid")
    return {
        "schema_version": "media_library_query_plan_v1",
        "original_query": str(payload.get("original_query") or ""),
        "exact_phrases": list(parsed.get("exact_phrases") or []),
        "optional_terms": list(parsed.get("optional_terms") or []),
        "negative_terms": list(parsed.get("negative_terms") or []),
        "orientation": str(payload.get("orientation") or "any"),
        "min_duration_ms": payload.get("min_duration_ms"),
        "max_duration_ms": payload.get("max_duration_ms"),
        "sources": list(payload.get("sources") or ["media_library"]),
        "planner_version": PLANNER_VERSION,
    }


class OpenCodeMediaLibrarySearchPlannerAdapter:
    """Production query planner using an approved read-only model alias."""

    def __init__(
        self,
        ctx: Any,
        *,
        client_factory: Callable[[Any, dict[str, Any], str], Any]
        | None = None,
    ) -> None:
        self.ctx = ctx
        self.client_factory = (
            client_factory or opencode_client_for_context
        )
        self.timeout_seconds = _environment_float(
            "OPENCREW_MEDIA_LIBRARY_SEARCH_PLANNER_TIMEOUT_SECONDS",
            8.0,
            minimum=0.1,
        )
        self.cache_ttl_seconds = _environment_float(
            "OPENCREW_MEDIA_LIBRARY_SEARCH_PLANNER_CACHE_TTL_SECONDS",
            900.0,
            minimum=1.0,
        )
        self.cache_max_entries = _environment_int(
            "OPENCREW_MEDIA_LIBRARY_SEARCH_PLANNER_CACHE_MAX_ENTRIES",
            256,
        )
        self._cache: OrderedDict[
            str, tuple[float, dict[str, Any]]
        ] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._model_semaphore = threading.BoundedSemaphore(
            _environment_int(
                "OPENCREW_MEDIA_LIBRARY_SEARCH_PLANNER_CONCURRENCY", 2
            )
        )

    def __call__(self, raw_payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(raw_payload)
        request_context = payload.pop("_request_context", {})
        if not isinstance(request_context, Mapping):
            raise PlannerCallError("planner_unavailable")
        policy = surface_policy(
            self.ctx, SURFACE_MEDIA_LIBRARY_SEARCH_PLANNER
        )
        if (
            str(policy.get("mode") or "").strip().lower() != "alias"
            or not bool(policy.get("alias_only"))
            or not bool(policy.get("read_only"))
        ):
            raise PlannerCallError("planner_unavailable")
        query = str(payload.get("original_query") or "").strip()
        max_prompt_chars = _environment_int(
            "OPENCREW_MEDIA_LIBRARY_SEARCH_PLANNER_MAX_PROMPT_CHARS",
            8000,
        )
        estimated_cost = _environment_int(
            "OPENCREW_MEDIA_LIBRARY_SEARCH_PLANNER_EST_COST_PER_CALL_MICROS",
            500,
        )
        max_estimated_cost = _environment_int(
            "OPENCREW_MEDIA_LIBRARY_SEARCH_PLANNER_MAX_EST_COST_MICROS",
            5000,
        )
        if (
            not query
            or len(query) > max_prompt_chars
            or estimated_cost > max_estimated_cost
        ):
            raise PlannerCallError("planner_quota_exceeded")

        cache_key = self._cache_key(payload, policy)
        cached = self._cache_read(cache_key)
        if cached is not None:
            self._metric(
                "media_library_search_planner_cache_hit_total", 1
            )
            return cached

        session = self._session_for(request_context)
        model: dict[str, str] | None = None
        started_at = int(time.time() * 1000)
        status = "failed"
        error_code = "planner_unavailable"
        try:
            client = self.client_factory(
                self.ctx,
                session,
                "查询规划模型服务尚未配置。",
            )
            catalog = _model_catalog(client)
            model, _masked = resolve_prompt_model_for_role(
                self.ctx,
                AUTH_ROLE_USER,
                SURFACE_MEDIA_LIBRARY_SEARCH_PLANNER,
                catalog,
                "",
                "",
                "Media library search planner",
            )
            result = self._invoke(
                client=client,
                model=model,
                payload=payload,
            )
            self._cache_write(cache_key, result)
            status = "ok"
            error_code = ""
            self._metric(
                "media_library_search_planner_model_call_total", 1
            )
            return result
        except PlannerCallError as exc:
            error_code = exc.code
            raise
        except HTTPError as exc:
            if exc.code in {402, 429}:
                error_code = "planner_quota_exceeded"
                raise PlannerCallError(error_code) from exc
            raise PlannerCallError("planner_unavailable") from exc
        except HTTPException as exc:
            raise PlannerCallError("planner_unavailable") from exc
        finally:
            if status != "ok":
                self._metric(
                    "media_library_search_planner_model_failure_total",
                    1,
                )
            self._record_usage(
                model=model,
                request_context=request_context,
                input_chars=len(query),
                estimated_cost=estimated_cost,
                started_at=started_at,
                status=status,
                error_code=error_code,
            )

    def _session_for(
        self, request_context: Mapping[str, Any]
    ) -> dict[str, Any]:
        session_id = 0
        target_task_id = request_context.get("target_task_id")
        if target_task_id not in (None, ""):
            try:
                target = MediaLibraryImportRepository(
                    self.ctx.engine
                ).get_target_task(int(target_task_id))
            except (TypeError, ValueError):
                target = None
            session_id = int((target or {}).get("session_id") or 0)
        if session_id <= 0:
            source_asset_id = str(
                request_context.get("source_asset_id") or ""
            ).strip()
            asset = (
                self.ctx.media_library_repo.get(source_asset_id)
                if source_asset_id
                else None
            )
            session_id = int((asset or {}).get("session_id") or 0)
        session = (
            self.ctx.session_repo.get(session_id)
            if session_id > 0
            else None
        )
        if (
            session is None
            or not str(session.get("workspace_dir") or "").strip()
        ):
            raise PlannerCallError("planner_unavailable")
        return session

    def _invoke(
        self,
        *,
        client: Any,
        model: dict[str, str],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        prompt = json.dumps(
            {
                "schema_version": "media_library_query_planner_input_v1",
                "query": str(payload.get("original_query") or ""),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        model_session_id = ""
        deadline = time.monotonic() + self.timeout_seconds
        started_at = int(time.time() * 1000)
        try:
            with self._model_semaphore:
                model_session = client.create_session(
                    "Media library search planner",
                    timeout=max(
                        1,
                        min(3, int(deadline - time.monotonic())),
                    ),
                )
                model_session_id = str(model_session.get("id") or "")
                if not model_session_id:
                    raise PlannerCallError("planner_unavailable")
                client.prompt_async(
                    model_session_id,
                    prompt,
                    model=model,
                    system=SYSTEM_PROMPT,
                    tools=DISABLED_MODEL_TOOLS,
                    parts=[{"type": "text", "text": prompt}],
                    timeout=max(
                        1,
                        min(3, int(deadline - time.monotonic())),
                    ),
                )
                assistant_text: str | None = None
                while time.monotonic() < deadline:
                    assistant_text = _last_completed_assistant(
                        client.messages(
                            model_session_id,
                            limit=20,
                            timeout=max(
                                1,
                                min(
                                    2,
                                    int(deadline - time.monotonic()),
                                ),
                            ),
                        ),
                        started_at,
                    )
                    if assistant_text:
                        break
                    time.sleep(0.2)
                if not assistant_text:
                    try:
                        client.abort(model_session_id)
                    except Exception:
                        pass
                    raise TimeoutError("search_planner_timeout")
                return _strict_model_output(assistant_text, payload)
        finally:
            if model_session_id:
                try:
                    client.delete_session(model_session_id, timeout=2)
                except Exception:
                    pass

    def _cache_key(
        self, payload: Mapping[str, Any], policy: Mapping[str, Any]
    ) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "query": str(payload.get("original_query") or ""),
                    "orientation": payload.get("orientation"),
                    "min_duration_ms": payload.get("min_duration_ms"),
                    "max_duration_ms": payload.get("max_duration_ms"),
                    "sources": list(payload.get("sources") or []),
                    "policy_version": str(policy.get("version") or ""),
                    "planner_version": PLANNER_VERSION,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _cache_read(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            created_at, value = cached
            if now - created_at > self.cache_ttl_seconds:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return dict(value)

    def _cache_write(self, key: str, value: Mapping[str, Any]) -> None:
        with self._cache_lock:
            self._cache[key] = (time.monotonic(), dict(value))
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_max_entries:
                self._cache.popitem(last=False)

    def _record_usage(
        self,
        *,
        model: dict[str, str] | None,
        request_context: Mapping[str, Any],
        input_chars: int,
        estimated_cost: int,
        started_at: int,
        status: str,
        error_code: str,
    ) -> None:
        recorder = getattr(self.ctx, "local_usage", None)
        if recorder is None or model is None:
            return
        try:
            recorder.record_with_result(
                provider=str(model.get("providerID") or ""),
                model_id=str(model.get("modelID") or ""),
                modality="text_to_text",
                proxy_policy="opencode_media_library_search_planner",
                status=status,
                task_id=request_context.get("target_task_id"),
                step_id="media_library.search_planner",
                idempotency_key=(
                    f"media-library-search-planner:{uuid.uuid4().hex}"
                ),
                units={
                    "model_call_count": 1,
                    "input_chars": max(0, int(input_chars)),
                },
                est_cost_micros=estimated_cost,
                error_code=error_code,
                started_at=started_at,
                finished_at=int(time.time() * 1000),
            )
        except Exception:
            pass

    def _metric(self, name: str, value: int) -> None:
        sink = getattr(self.ctx, "media_library_metric", None)
        if not callable(sink):
            return
        try:
            sink(name, int(value))
        except Exception:
            pass
