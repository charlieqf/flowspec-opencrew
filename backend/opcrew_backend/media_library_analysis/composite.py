from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from fastapi import HTTPException

from ..context import now_ms
from ..media_library_features import require_media_library_feature
from ..model_policy import (
    SURFACE_MEDIA_LIBRARY_COMPOSITE,
    resolve_prompt_model_for_role,
    surface_policy,
)
from ..routes.auth import AUTH_ROLE_USER
from ..services.opencode_runtime import opencode_client_for_context
from ..tool_sessions import (
    PrepareInputFile,
    PrepareSessionVariablesInput,
    ToolSessionRunner,
    ToolSessionService,
)
from ..tool_sessions.registry_normalizer import normalize_registry_file
from ..tool_sessions.schemas import PromptManifest, ToolResult
from .composite_contracts import (
    CANDIDATE_SCHEMA_VERSION,
    INDEX_PATH,
    INPUT_DIALOGUE_REL,
    INPUT_MANIFEST_REL,
    INPUT_SCHEMA_VERSION,
    INPUT_SEMANTIC_REL,
    INPUT_STRUCTURE_REL,
    QUALITY_PATH,
    RESULT_PATH,
    RESULT_SCHEMA_VERSION,
    SEARCH_MANIFEST_PATH,
    VIRTUAL_CLIPS_PATH,
    CompositeValidationError,
    publish_composite_contract,
)
from .contracts import result_hash
from .lifecycle import finalize_analysis_tool_session, result_sync_error
from .run_repository import AnalysisRunRepository
from .visual_semantic import (
    DISABLED_MODEL_TOOLS,
    _extract_json_object,
    _int_setting,
    _last_completed_assistant,
    _model_catalog,
    _read_object,
    _write_json,
)


OPENCREW_ROOT = Path(__file__).resolve().parents[3]
OPEN_CUT_REGISTRY = (
    OPENCREW_ROOT / "ToolLibrary" / "OpenCut_V1" / "tool_registry.json"
)
WORKFLOW_ID = "open-cut-v1-composite"
TOOL_ID = "04_01"
STEP_ID = "S1"
PROMPT_VERSION_DEFAULT = "composite_prompt_v6"
MODEL_CONFIG_DEFAULT = "composite_default_v1"
TOTAL_STEPS = 2
MODEL_ITEM_FIELDS = {
    "start_ms",
    "end_ms",
    "title",
    "summary",
    "keywords",
    "people",
    "objects",
    "scene",
    "action",
    "dialogue_refs",
    "visual_refs",
    "visual_claim_refs",
    "boundary_reasons",
    "confidence",
    "needs_review",
}
SYSTEM_PROMPT = """Create a compact composite index from the supplied,
already-published dialogue and visual-semantic JSON. Return strict JSON:
{"items":[...]}, no markdown. Each item may contain only start_ms, end_ms,
title, summary, keywords, people, objects, scene, action, dialogue_refs,
visual_refs, visual_claim_refs, boundary_reasons, confidence, needs_review.
Write all newly generated user-facing text, including title, summary, keywords,
and boundary_reasons, in concise Simplified Chinese. Preserve exact upstream
factual values, stable ids, and evidence references without translation.
Times must use supplied integer-ms boundaries. References must be supplied
stable IDs and must be fully contained by the chosen range. Use
reference_ranges to calculate the range instead of estimating it. Visual
facts may only copy exact keys from visual_evidence_catalog. For every copied
people/object/scene value, visual_claim_refs must contain at least one
supporting ref listed for that exact value in the catalog. Drop a value that
is absent from the catalog. All dialogue_refs, visual_refs, and
visual_claim_refs values must use the
top-level fragment_id of an upstream item; never use keyframe_refs or
claim_evidence IDs as composite refs. Never infer identity or sensitive
attributes. Because visual sampling is four sparse ordered keyframes per
at-most-15-second fragment, it does not prove continuous motion: action must
be null and visual_claim_refs.action must be [].
For each item, set start_ms to the minimum start_ms and end_ms to the maximum
end_ms across all of its dialogue_refs and visual_refs; this keeps every
reference inside the range. Sort items and do not overlap ranges. Every
visual_claim_refs.<field> list must be a subset of that same item's
visual_refs, and every copied value must have at least one supporting ref in
that field's claim-ref list. If a people/object/scene value has no such
supporting ref, drop the value. Leave a field's claim-ref list empty when the
field itself is empty. Remove an item
instead of returning it when it cannot retain any valid dialogue or visual
reference.
When the source is at least 30 seconds and visual_semantic contains three or
more non-overlapping visual analysis windows, do not collapse the whole video
into one item. Return at least two useful items. Prefer one item per visual
window or a small adjacent semantic group. Dialogue refs that cross a visual
window boundary may be omitted from composite items because they remain
available in the dialogue index; never create overlapping composite ranges to
force them in.
Do not invent dialogue_text, visual_summary, keyframe refs, IDs, asset fields,
or durations; the backend owns those fields. Field types are strict:
start_ms/end_ms are integers; title/summary are strings; keywords, people,
objects, dialogue_refs, visual_refs, boundary_reasons are arrays of strings;
scene is exactly one upstream scene string or null, never an array; action is
null; visual_claim_refs is an object with people/objects/scene/action arrays;
confidence is a finite number from 0 to 1; needs_review is a boolean."""

REPAIR_INSTRUCTION = """Return one corrected full candidate. Apply the full
contract, not only the reported first error. Before returning, audit every
reference against reference_ranges and every people/object/scene value
against visual_evidence_catalog. For every item: (1) use only
top-level fragment_id values present in the supplied dialogue or
visual_semantic items, never keyframe_refs or claim_evidence IDs; (2) set
start_ms=min(ref.start_ms) and end_ms=max(ref.end_ms) across all dialogue_refs
and visual_refs; (3) keep each visual_claim_refs field strictly within the
same item's visual_refs and reference only visual items containing the exact
each people/object/scene value according to visual_evidence_catalog, removing
any value absent from the catalog; (4) keep action=null and its refs=[]; (5) sort
items, merge or remove any item needed to avoid overlap, and remove any item
with no remaining evidence refs; (6) when the source is at least 30 seconds
and has three or more visual analysis windows, return at least two useful,
non-overlapping items instead of one whole-video item. Return strict
{\"items\":[...]} JSON with
all allowed fields and no markdown. Keep all newly generated user-facing text
in concise Simplified Chinese. The repair payload contains a
full_candidate_audit; fix every reported item, not just validation_error."""

LONG_SOURCE_MIN_MS = 30_000
LONG_SOURCE_MIN_VISUAL_WINDOWS = 3


class CompositeBlocked(RuntimeError):
    def __init__(
        self, code: str, user_message: str, suggested_action: str
    ) -> None:
        self.code = code
        self.user_message = user_message
        self.suggested_action = suggested_action
        super().__init__(user_message)

    def payload(self) -> dict[str, str]:
        return {
            "code": self.code,
            "user_message": self.user_message,
            "suggested_action": self.suggested_action,
        }


class CompositeModelOutputError(ValueError):
    def __init__(self, raw_text: str, input_chars: int) -> None:
        self.raw_text = raw_text
        self.input_chars = input_chars
        super().__init__("composite_model_json_invalid")


def _orientation(width: Any, height: Any) -> str:
    try:
        w = int(width or 0)
        h = int(height or 0)
    except (TypeError, ValueError):
        return "unknown"
    if w <= 0 or h <= 0:
        return "unknown"
    if w == h:
        return "square"
    return "landscape" if w > h else "portrait"


def _join_unique(values: list[str]) -> str | None:
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return " ".join(result) if result else None


def _visual_evidence_catalog(
    semantic: Mapping[str, Any],
) -> dict[str, dict[str, list[str]]]:
    catalog: dict[str, dict[str, list[str]]] = {
        "people": {},
        "objects": {},
        "scene": {},
    }
    for item in semantic.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        fragment_id = str(item.get("fragment_id") or "").strip()
        if not fragment_id:
            continue
        for field in catalog:
            raw = item.get(field)
            values = raw if isinstance(raw, list) else [raw]
            for value in values:
                normalized = str(value or "").strip()
                if not normalized:
                    continue
                refs = catalog[field].setdefault(normalized, [])
                if fragment_id not in refs:
                    refs.append(fragment_id)
    return catalog


class CompositeAnalysisToolAdapter:
    _model_semaphore = threading.BoundedSemaphore(
        _int_setting(
            "OPENCREW_MEDIA_LIBRARY_COMPOSITE_MODEL_CONCURRENCY", 1
        )
    )

    def __init__(
        self,
        *,
        ctx: Any,
        session: dict[str, Any],
        asset: dict[str, Any],
        analysis_run_id: str,
        upstream: dict[str, dict[str, Any]],
        composite_prompt_version: str,
        model_config_id: str,
    ) -> None:
        self.ctx = ctx
        self.session = session
        self.asset = asset
        self.analysis_run_id = analysis_run_id
        self.upstream = upstream
        self.composite_prompt_version = composite_prompt_version
        self.model_config_id = model_config_id
        self.cache_root = (
            Path(str(ctx.data_dir)) / "cache" / "media_library_composite"
        )

    def _resolve_model(self):
        policy = surface_policy(
            self.ctx, SURFACE_MEDIA_LIBRARY_COMPOSITE
        )
        if (
            str(policy.get("mode") or "").strip().lower() != "alias"
            or not bool(policy.get("alias_only"))
            or not bool(policy.get("read_only"))
        ):
            raise CompositeBlocked(
                "composite_model_policy_invalid",
                "综合分析模型策略缺少只读别名门禁。",
                "请由管理员补齐 media_library.composite 策略。",
            )
        try:
            client = opencode_client_for_context(
                self.ctx,
                self.session,
                "综合分析文本模型服务尚未配置。",
            )
            catalog = _model_catalog(client)
            model, _masked = resolve_prompt_model_for_role(
                self.ctx,
                AUTH_ROLE_USER,
                SURFACE_MEDIA_LIBRARY_COMPOSITE,
                catalog,
                "",
                "",
                "Composite",
            )
            return client, model
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            raise CompositeBlocked(
                str(
                    detail.get("code")
                    or "composite_model_configuration_unavailable"
                ),
                str(
                    detail.get("user_message")
                    or "没有可用的已批准综合分析模型。"
                ),
                str(
                    detail.get("suggested_action")
                    or "请由管理员配置综合分析模型别名后重试。"
                ),
            ) from exc
        except Exception as exc:
            raise CompositeBlocked(
                "composite_model_configuration_unavailable",
                "综合分析模型服务尚未配置或不可用。",
                "请完成 OpenCode 与已批准文本模型配置后重试。",
            ) from exc

    def _cache_key(self, model: Mapping[str, str]) -> str:
        return result_hash(
            {
                "dialogue_result_hash": self.upstream["dialogue"][
                    "result_hash"
                ],
                "visual_structure_result_hash": self.upstream[
                    "visual_structure"
                ]["result_hash"],
                "visual_semantic_result_hash": self.upstream[
                    "visual_semantic"
                ]["result_hash"],
                "composite_prompt_version": self.composite_prompt_version,
                "model_config_id": self.model_config_id,
                "resolved_model_target_hash": hashlib.sha256(
                    (
                        f"{model.get('providerID', '')}\0"
                        f"{model.get('modelID', '')}"
                    ).encode("utf-8")
                ).hexdigest(),
                "schema_version": RESULT_SCHEMA_VERSION,
            }
        )

    def _cache_read(self, cache_key: str) -> dict[str, Any] | None:
        path = self.cache_root / f"{cache_key}.json"
        if not path.is_file():
            return None
        try:
            value = _read_object(path)
        except Exception:
            return None
        candidate = value.get("candidate")
        if value.get("cache_key") != cache_key or not isinstance(
            candidate, dict
        ):
            return None
        return dict(candidate)

    def _cache_write(
        self, cache_key: str, candidate: Mapping[str, Any]
    ) -> None:
        _write_json(
            self.cache_root / f"{cache_key}.json",
            {
                "schema_version": "media_library_composite_cache_v1",
                "cache_key": cache_key,
                "candidate": dict(candidate),
            },
        )

    @staticmethod
    def _merge_candidate(
        *,
        model_candidate: Mapping[str, Any],
        asset_id: str,
        dialogue: Mapping[str, Any],
        semantic: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw_items = model_candidate.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise CompositeValidationError("composite_fragments_empty")
        dialogue_by_id = {
            str(item.get("fragment_id") or ""): dict(item)
            for item in (dialogue.get("items") or [])
            if isinstance(item, dict)
        }
        semantic_by_id = {
            str(item.get("fragment_id") or ""): dict(item)
            for item in (semantic.get("items") or [])
            if isinstance(item, dict)
        }
        merged: list[dict[str, Any]] = []
        sorted_items = sorted(
            (
                dict(item)
                for item in raw_items
                if isinstance(item, dict)
            ),
            key=lambda item: (
                int(item.get("start_ms") or 0),
                int(item.get("end_ms") or 0),
            ),
        )
        for index, raw in enumerate(sorted_items, start=1):
            unknown = set(raw) - MODEL_ITEM_FIELDS
            if unknown:
                raise CompositeValidationError(
                    "composite_model_unknown_field",
                    ",".join(sorted(unknown)),
                )
            dialogue_refs = raw.get("dialogue_refs")
            visual_refs = raw.get("visual_refs")
            if not isinstance(dialogue_refs, list):
                dialogue_refs = []
            if not isinstance(visual_refs, list):
                visual_refs = []
            expected_dialogue = _join_unique(
                [
                    str(
                        dialogue_by_id.get(str(ref), {}).get(
                            "dialogue_text"
                        )
                        or ""
                    )
                    for ref in dialogue_refs
                ]
            )
            expected_visual = _join_unique(
                [
                    str(
                        semantic_by_id.get(str(ref), {}).get(
                            "visual_summary"
                        )
                        or ""
                    )
                    for ref in visual_refs
                ]
            )
            keyframe_refs: list[str] = []
            for ref in visual_refs:
                for keyframe_ref in (
                    semantic_by_id.get(str(ref), {}).get("keyframe_refs")
                    or []
                ):
                    keyframe_ref = str(keyframe_ref)
                    if keyframe_ref not in keyframe_refs:
                        keyframe_refs.append(keyframe_ref)
            start_ms = raw.get("start_ms")
            end_ms = raw.get("end_ms")
            merged.append(
                {
                    **raw,
                    "fragment_id": f"composite_{index:04d}",
                    "asset_id": asset_id,
                    "scheme": "composite",
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "duration_ms": (
                        int(end_ms) - int(start_ms)
                        if isinstance(start_ms, int)
                        and not isinstance(start_ms, bool)
                        and isinstance(end_ms, int)
                        and not isinstance(end_ms, bool)
                        else None
                    ),
                    "dialogue_text": expected_dialogue,
                    "visual_summary": expected_visual,
                    "action": None,
                    "keyframe_refs": keyframe_refs,
                }
            )
            claim_refs = merged[-1].get("visual_claim_refs")
            if isinstance(claim_refs, dict):
                claim_refs = dict(claim_refs)
                claim_refs["action"] = []
                merged[-1]["visual_claim_refs"] = claim_refs
        return {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "items": merged,
        }

    @staticmethod
    def _validate_segmentation_granularity(
        *,
        candidate: Mapping[str, Any],
        semantic: Mapping[str, Any],
        source_duration_ms: int,
    ) -> None:
        visual_count = len(
            [
                item
                for item in (semantic.get("items") or [])
                if isinstance(item, Mapping)
            ]
        )
        composite_count = len(
            [
                item
                for item in (candidate.get("items") or [])
                if isinstance(item, Mapping)
            ]
        )
        if (
            source_duration_ms >= LONG_SOURCE_MIN_MS
            and visual_count >= LONG_SOURCE_MIN_VISUAL_WINDOWS
            and composite_count < 2
        ):
            raise CompositeValidationError(
                "composite_segments_overmerged",
                (
                    f"source_duration_ms={source_duration_ms},"
                    f"visual_windows={visual_count},"
                    f"composite_items={composite_count}"
                ),
            )

    def _model_call(
        self,
        *,
        client: Any,
        model_session_id: str,
        model: dict[str, str],
        prompt_payload: dict[str, Any],
        repair_error: str = "",
        prior_candidate: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], int]:
        payload = dict(prompt_payload)
        if repair_error:
            payload["repair"] = {
                "validation_error": repair_error,
                "invalid_candidate": dict(prior_candidate or {}),
                "full_candidate_audit": self._repair_candidate_audit(
                    prior_candidate or {}, prompt_payload
                ),
                "instruction": REPAIR_INSTRUCTION,
            }
        prompt = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
        started_at = now_ms()
        client.prompt_async(
            model_session_id,
            prompt,
            model=model,
            system=SYSTEM_PROMPT,
            tools=DISABLED_MODEL_TOOLS,
            parts=[{"type": "text", "text": prompt}],
        )
        deadline = time.monotonic() + _int_setting(
            "OPENCREW_MEDIA_LIBRARY_COMPOSITE_MODEL_TIMEOUT_SECONDS", 240
        )
        assistant_text: str | None = None
        while time.monotonic() < deadline:
            assistant_text = _last_completed_assistant(
                client.messages(model_session_id, limit=40),
                started_at,
            )
            if assistant_text:
                break
            time.sleep(0.5)
        if not assistant_text:
            try:
                client.abort(model_session_id)
            except Exception:
                pass
            raise TimeoutError("composite_model_timeout")
        try:
            return _extract_json_object(assistant_text), len(prompt)
        except (ValueError, json.JSONDecodeError) as exc:
            raise CompositeModelOutputError(
                assistant_text, len(prompt)
            ) from exc

    @staticmethod
    def _repair_candidate_audit(
        candidate: Mapping[str, Any],
        prompt_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        ranges = prompt_payload.get("reference_ranges")
        if not isinstance(ranges, Mapping):
            ranges = {}
        catalog = prompt_payload.get("visual_evidence_catalog")
        if not isinstance(catalog, Mapping):
            catalog = {}
        raw_items = candidate.get("items")
        if not isinstance(raw_items, list):
            return {"candidate_items_invalid": True, "item_issues": []}
        item_issues: list[dict[str, Any]] = []
        previous_expected_end = -1
        for index, raw in enumerate(raw_items, start=1):
            if not isinstance(raw, Mapping):
                item_issues.append(
                    {"item_index": index, "item_must_be_object": True}
                )
                continue
            dialogue_refs = raw.get("dialogue_refs")
            visual_refs = raw.get("visual_refs")
            dialogue_refs = (
                dialogue_refs if isinstance(dialogue_refs, list) else []
            )
            visual_refs = visual_refs if isinstance(visual_refs, list) else []
            refs = [str(ref) for ref in (*dialogue_refs, *visual_refs)]
            unknown_refs = [ref for ref in refs if ref not in ranges]
            known_ranges = [
                ranges[ref]
                for ref in refs
                if ref in ranges
                and isinstance(ranges[ref], list)
                and len(ranges[ref]) == 2
            ]
            issue: dict[str, Any] = {"item_index": index}
            if unknown_refs:
                issue["unknown_refs"] = unknown_refs
            if known_ranges:
                expected_start = min(int(value[0]) for value in known_ranges)
                expected_end = max(int(value[1]) for value in known_ranges)
                if (
                    raw.get("start_ms") != expected_start
                    or raw.get("end_ms") != expected_end
                ):
                    issue["reference_closure"] = {
                        "actual": [raw.get("start_ms"), raw.get("end_ms")],
                        "required_exact": [expected_start, expected_end],
                    }
                if expected_start < previous_expected_end:
                    issue["overlaps_previous_item"] = True
                previous_expected_end = expected_end
            if raw.get("scene") is not None and not isinstance(
                raw.get("scene"), str
            ):
                issue["scene_must_be_one_string_or_null"] = True
            claim_refs = raw.get("visual_claim_refs")
            claim_refs = claim_refs if isinstance(claim_refs, Mapping) else {}
            unsupported: dict[str, list[str]] = {}
            for field in ("people", "objects", "scene"):
                raw_values = raw.get(field)
                values = (
                    raw_values
                    if isinstance(raw_values, list)
                    else [raw_values]
                )
                field_catalog = catalog.get(field)
                field_catalog = (
                    field_catalog
                    if isinstance(field_catalog, Mapping)
                    else {}
                )
                refs_for_field = claim_refs.get(field)
                refs_for_field = (
                    refs_for_field
                    if isinstance(refs_for_field, list)
                    else []
                )
                unsupported_values = []
                for value in values:
                    normalized = str(value or "").strip()
                    if not normalized:
                        continue
                    supporting = field_catalog.get(normalized)
                    supporting = (
                        supporting if isinstance(supporting, list) else []
                    )
                    if not set(map(str, refs_for_field)).intersection(
                        map(str, supporting)
                    ):
                        unsupported_values.append(normalized)
                if unsupported_values:
                    unsupported[field] = unsupported_values
            if unsupported:
                issue["unsupported_visual_facts"] = unsupported
            if len(issue) > 1:
                item_issues.append(issue)
        return {"candidate_items_invalid": False, "item_issues": item_issues}

    def _record_usage(
        self,
        *,
        model: dict[str, str],
        call_index: int,
        started_at: int,
        input_chars: int,
        status: str,
        error_code: str = "",
    ) -> tuple[str, str]:
        result = self.ctx.local_usage.record_with_result(
            provider=str(model.get("providerID") or ""),
            model_id=str(model.get("modelID") or ""),
            modality="text_to_text",
            proxy_policy="opencode_composite",
            status=status,
            task_id=self.asset.get("task_id"),
            attempt_id=self.analysis_run_id,
            step_id=TOOL_ID,
            idempotency_key=(
                f"media-library-composite:{self.analysis_run_id}:"
                f"{call_index}"
            ),
            units={"model_call_count": 1, "input_chars": input_chars},
            est_cost_micros=_int_setting(
                "OPENCREW_MEDIA_LIBRARY_COMPOSITE_EST_COST_PER_CALL_MICROS",
                3000,
            ),
            error_code=error_code,
            started_at=started_at,
            finished_at=now_ms(),
        )
        return result.request_id, result.local_usage_id

    def run(
        self,
        *,
        tool: dict[str, Any],
        step: Any,
        paths: Any,
        tool_dir: Path,
    ) -> ToolResult:
        try:
            return self._run(
                tool=tool, step=step, paths=paths, tool_dir=tool_dir
            )
        except CompositeBlocked as exc:
            return ToolResult(
                tool_id=TOOL_ID,
                tool_name=str(tool.get("name") or ""),
                step_id=str(step.step_id),
                status="blocked",
                outputs={"error": exc.payload()},
                errors=[exc.user_message],
            )

    def _run(
        self,
        *,
        tool: dict[str, Any],
        step: Any,
        paths: Any,
        tool_dir: Path,
    ) -> ToolResult:
        dialogue = _read_object(paths.root / INPUT_DIALOGUE_REL)
        structure = _read_object(paths.root / INPUT_STRUCTURE_REL)
        semantic = _read_object(paths.root / INPUT_SEMANTIC_REL)
        total_inputs = len(dialogue.get("items") or []) + len(
            semantic.get("items") or []
        )
        if total_inputs > _int_setting(
            "OPENCREW_MEDIA_LIBRARY_COMPOSITE_MAX_INPUT_FRAGMENTS", 1000
        ):
            raise CompositeBlocked(
                "quota_exceeded",
                "综合分析输入片段超过本次运行上限。",
                "减少输入片段或由管理员调整综合分析配额。",
            )
        client, model = self._resolve_model()
        model_session = client.create_session(
            f"Media library composite {self.analysis_run_id}"
        )
        model_session_id = str(model_session["id"])
        self.ctx.media_analysis_run_repo.set_model_session(
            self.analysis_run_id,
            model_session_id=model_session_id,
            timestamp=now_ms(),
        )
        prompt_payload = {
            "schema_version": "media_library_composite_prompt_input_v1",
            "source": {
                "duration_ms": self.asset.get("duration_ms"),
                "width": self.asset.get("width"),
                "height": self.asset.get("height"),
                "orientation": _orientation(
                    self.asset.get("width"), self.asset.get("height")
                ),
            },
            "dialogue": dialogue,
            "visual_structure": structure,
            "visual_semantic": semantic,
            "candidate_boundaries_ms": sorted(
                {
                    int(item.get(field) or 0)
                    for payload in (dialogue, semantic)
                    for item in (payload.get("items") or [])
                    if isinstance(item, dict)
                    for field in ("start_ms", "end_ms")
                }
            ),
            "reference_ranges": {
                str(item["fragment_id"]): [
                    int(item["start_ms"]),
                    int(item["end_ms"]),
                ]
                for payload in (dialogue, semantic)
                for item in (payload.get("items") or [])
                if isinstance(item, dict)
                and str(item.get("fragment_id") or "").strip()
            },
            "visual_evidence_catalog": _visual_evidence_catalog(semantic),
            "upstream_refs": {
                scheme: {
                    "analysis_run_id": str(run["analysis_run_id"]),
                    "result_hash": str(run["result_hash"]),
                }
                for scheme, run in self.upstream.items()
            },
        }
        cache_key = self._cache_key(model)
        cached = self._cache_read(cache_key)
        calls = 0
        repair_count = 0
        audits: list[dict[str, Any]] = []
        candidate: dict[str, Any]
        if cached is not None:
            candidate = cached
            cache_hits = 1
        else:
            cache_hits = 0
            max_calls = _int_setting(
                "OPENCREW_MEDIA_LIBRARY_COMPOSITE_MAX_CALLS_PER_RUN", 2
            )
            max_cost = _int_setting(
                "OPENCREW_MEDIA_LIBRARY_COMPOSITE_MAX_EST_COST_MICROS",
                100_000,
            )
            cost_per_call = _int_setting(
                "OPENCREW_MEDIA_LIBRARY_COMPOSITE_EST_COST_PER_CALL_MICROS",
                3000,
            )
            if max_calls < 1 or cost_per_call > max_cost:
                raise CompositeBlocked(
                    "quota_exceeded",
                    "综合分析模型调用超过已配置配额。",
                    "由管理员调整调用次数或成本上限。",
                )
            call_started = now_ms()
            parse_error: CompositeValidationError | None = None
            try:
                with self._model_semaphore:
                    try:
                        raw_candidate, input_chars = self._model_call(
                            client=client,
                            model_session_id=model_session_id,
                            model=model,
                            prompt_payload=prompt_payload,
                        )
                    except CompositeModelOutputError as exc:
                        raw_candidate = {}
                        input_chars = exc.input_chars
                        parse_error = CompositeValidationError(
                            "composite_model_json_invalid"
                        )
            except Exception as exc:
                calls += 1
                try:
                    self._record_usage(
                        model=model,
                        call_index=calls,
                        started_at=call_started,
                        input_chars=0,
                        status="failed",
                        error_code=str(exc).split(":", 1)[0],
                    )
                except Exception:
                    pass
                raise
            calls += 1
            request_id, usage_id = self._record_usage(
                model=model,
                call_index=calls,
                started_at=call_started,
                input_chars=input_chars,
                status="ok",
            )
            audits.append(
                {
                    "request_id": request_id,
                    "local_usage_id": usage_id,
                    "repair": False,
                }
            )
            try:
                if parse_error is not None:
                    raise parse_error
                candidate = self._merge_candidate(
                    model_candidate=raw_candidate,
                    asset_id=str(self.asset["asset_id"]),
                    dialogue=dialogue,
                    semantic=semantic,
                )
                self._validate_segmentation_granularity(
                    candidate=candidate,
                    semantic=semantic,
                    source_duration_ms=int(
                        self.asset.get("duration_ms") or 0
                    ),
                )
                publish_composite_contract(
                    tool_root=paths.root,
                    asset_id=str(self.asset["asset_id"]),
                    source_version=str(self.asset["content_sha256"]),
                    analysis_run_id=self.analysis_run_id,
                    dialogue_run_id=str(
                        self.upstream["dialogue"]["analysis_run_id"]
                    ),
                    dialogue_result_hash=str(
                        self.upstream["dialogue"]["result_hash"]
                    ),
                    visual_structure_run_id=str(
                        self.upstream["visual_structure"][
                            "analysis_run_id"
                        ]
                    ),
                    visual_structure_result_hash=str(
                        self.upstream["visual_structure"]["result_hash"]
                    ),
                    visual_semantic_run_id=str(
                        self.upstream["visual_semantic"][
                            "analysis_run_id"
                        ]
                    ),
                    visual_semantic_result_hash=str(
                        self.upstream["visual_semantic"]["result_hash"]
                    ),
                    composite_prompt_version=self.composite_prompt_version,
                    model_config_id=self.model_config_id,
                    candidate=candidate,
                    write=False,
                )
            except (CompositeValidationError, ValueError) as first_error:
                if max_calls < 2 or 2 * cost_per_call > max_cost:
                    raise CompositeBlocked(
                        "quota_exceeded",
                        "综合分析结构化修复超过已配置配额。",
                        "由管理员调整配额或修正模型配置。",
                    ) from first_error
                repair_started = now_ms()
                repair_parse_error: CompositeValidationError | None = None
                try:
                    with self._model_semaphore:
                        try:
                            repaired, input_chars = self._model_call(
                                client=client,
                                model_session_id=model_session_id,
                                model=model,
                                prompt_payload=prompt_payload,
                                repair_error=str(first_error),
                                prior_candidate=raw_candidate,
                            )
                        except CompositeModelOutputError as exc:
                            repaired = {}
                            input_chars = exc.input_chars
                            repair_parse_error = CompositeValidationError(
                                "composite_model_json_invalid"
                            )
                except Exception as exc:
                    calls += 1
                    try:
                        self._record_usage(
                            model=model,
                            call_index=calls,
                            started_at=repair_started,
                            input_chars=0,
                            status="failed",
                            error_code=str(exc).split(":", 1)[0],
                        )
                    except Exception:
                        pass
                    raise
                calls += 1
                request_id, usage_id = self._record_usage(
                    model=model,
                    call_index=calls,
                    started_at=repair_started,
                    input_chars=input_chars,
                    status="ok",
                )
                audits.append(
                    {
                        "request_id": request_id,
                        "local_usage_id": usage_id,
                        "repair": True,
                    }
                )
                repair_count = 1
                try:
                    if repair_parse_error is not None:
                        raise repair_parse_error
                    candidate = self._merge_candidate(
                        model_candidate=repaired,
                        asset_id=str(self.asset["asset_id"]),
                        dialogue=dialogue,
                        semantic=semantic,
                    )
                    self._validate_segmentation_granularity(
                        candidate=candidate,
                        semantic=semantic,
                        source_duration_ms=int(
                            self.asset.get("duration_ms") or 0
                        ),
                    )
                    publish_composite_contract(
                        tool_root=paths.root,
                        asset_id=str(self.asset["asset_id"]),
                        source_version=str(
                            self.asset["content_sha256"]
                        ),
                        analysis_run_id=self.analysis_run_id,
                        dialogue_run_id=str(
                            self.upstream["dialogue"]["analysis_run_id"]
                        ),
                        dialogue_result_hash=str(
                            self.upstream["dialogue"]["result_hash"]
                        ),
                        visual_structure_run_id=str(
                            self.upstream["visual_structure"][
                                "analysis_run_id"
                            ]
                        ),
                        visual_structure_result_hash=str(
                            self.upstream["visual_structure"][
                                "result_hash"
                            ]
                        ),
                        visual_semantic_run_id=str(
                            self.upstream["visual_semantic"][
                                "analysis_run_id"
                            ]
                        ),
                        visual_semantic_result_hash=str(
                            self.upstream["visual_semantic"][
                                "result_hash"
                            ]
                        ),
                        composite_prompt_version=(
                            self.composite_prompt_version
                        ),
                        model_config_id=self.model_config_id,
                        candidate=candidate,
                        write=False,
                    )
                except (CompositeValidationError, ValueError) as second_error:
                    raise CompositeValidationError(
                        "composite_structured_repair_exhausted",
                        (
                            f"{getattr(first_error, 'code', first_error)},"
                            f"{getattr(second_error, 'code', second_error)}"
                        ),
                    ) from second_error
            self._cache_write(cache_key, candidate)
        self._validate_segmentation_granularity(
            candidate=candidate,
            semantic=semantic,
            source_duration_ms=int(self.asset.get("duration_ms") or 0),
        )
        published, digest, result_path = publish_composite_contract(
            tool_root=paths.root,
            asset_id=str(self.asset["asset_id"]),
            source_version=str(self.asset["content_sha256"]),
            analysis_run_id=self.analysis_run_id,
            dialogue_run_id=str(
                self.upstream["dialogue"]["analysis_run_id"]
            ),
            dialogue_result_hash=str(
                self.upstream["dialogue"]["result_hash"]
            ),
            visual_structure_run_id=str(
                self.upstream["visual_structure"]["analysis_run_id"]
            ),
            visual_structure_result_hash=str(
                self.upstream["visual_structure"]["result_hash"]
            ),
            visual_semantic_run_id=str(
                self.upstream["visual_semantic"]["analysis_run_id"]
            ),
            visual_semantic_result_hash=str(
                self.upstream["visual_semantic"]["result_hash"]
            ),
            composite_prompt_version=self.composite_prompt_version,
            model_config_id=self.model_config_id,
            candidate=candidate,
        )
        quality = _read_object(paths.root / QUALITY_PATH)
        quality.update(
            {
                "cache_hit_count": cache_hits,
                "model_call_count": calls,
                "structured_repair_count": repair_count,
            }
        )
        _write_json(paths.root / QUALITY_PATH, quality)
        prompt_dir = tool_dir / "Prompt"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        (prompt_dir / "System.txt").write_text(
            SYSTEM_PROMPT, encoding="utf-8"
        )
        _write_json(
            prompt_dir / "PromptManifest.json",
            PromptManifest(
                tool_use_session_id=paths.tool_use_session_id,
                step_id=str(step.step_id),
                tool_id=TOOL_ID,
                prompts=[
                    {
                        "prompt_version": self.composite_prompt_version,
                        "prompt_sha256": hashlib.sha256(
                            SYSTEM_PROMPT.encode("utf-8")
                        ).hexdigest(),
                    }
                ],
                references=[
                    {
                        scheme: {
                            "analysis_run_id": str(run["analysis_run_id"]),
                            "result_hash": str(run["result_hash"]),
                        }
                        for scheme, run in self.upstream.items()
                    }
                ],
                model_calls=audits,
            ).model_dump(),
        )
        return ToolResult(
            tool_id=TOOL_ID,
            tool_name=str(tool.get("name") or ""),
            step_id=str(step.step_id),
            status="completed",
            outputs={
                "schema_version": str(published["schema_version"]),
                "result_hash": digest,
                "fragment_count": len(published["items"]),
                "cache_hit_count": cache_hits,
                "model_call_count": calls,
                "structured_repair_count": repair_count,
            },
            result_paths=[
                result_path,
                INDEX_PATH,
                VIRTUAL_CLIPS_PATH,
                SEARCH_MANIFEST_PATH,
                QUALITY_PATH,
            ],
        )


class CompositeAnalysisService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.asset_repo = ctx.media_library_repo
        self.task_repo = ctx.media_library_task_repo
        self.run_repo = getattr(ctx, "media_analysis_run_repo", None)
        if self.run_repo is None and getattr(ctx, "engine", None) is not None:
            self.run_repo = AnalysisRunRepository(ctx.engine)

    def start(
        self,
        asset_id: str,
        *,
        force: bool = False,
        prompt_version: str = PROMPT_VERSION_DEFAULT,
    ) -> dict[str, Any]:
        require_media_library_feature("analysis_runs")
        require_media_library_feature("composite")
        asset = self.asset_repo.get(asset_id)
        if asset is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "media_asset_not_found",
                    "user_message": "素材不存在或已删除。",
                },
            )
        task = self.task_repo.get_by_asset(asset_id)
        if task is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "open_cut_task_missing",
                    "user_message": "素材对应的 OpenCut Task 不存在。",
                },
            )
        upstream = {
            scheme: self.run_repo.current(asset_id, scheme)
            for scheme in (
                "dialogue",
                "visual_structure",
                "visual_semantic",
            )
        }
        missing = [
            scheme
            for scheme, run in upstream.items()
            if run is None or str(run.get("status") or "") != "ready"
        ]
        if missing:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_upstream_missing",
                    "user_message": "综合分析需要先完成对白、画面结构和画面语义分析。",
                    "suggested_action": "先完成缺失的分析后重试。",
                    "metadata": {"missing_schemes": missing},
                },
            )
        current = self.run_repo.current(asset_id, "composite")
        if (
            current is not None
            and str(current.get("status") or "") == "ready"
            and not force
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_result_exists",
                    "user_message": "当前综合分析已经完成。",
                },
            )
        if str(task.get("composite_status") or "") in {
            "queued",
            "running",
        }:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_run_active",
                    "user_message": "综合分析已经在运行。",
                },
            )
        duration_ms = asset.get("duration_ms")
        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or duration_ms <= 0
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "media_duration_missing",
                    "user_message": "素材缺少可信时长，不能运行综合分析。",
                },
            )
        session_id = int(task.get("session_id") or 0)
        session = self.ctx.session_repo.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "media_session_missing",
                    "user_message": "素材 Session 不存在。",
                },
            )
        model_config_id = (
            str(
                surface_policy(
                    self.ctx, SURFACE_MEDIA_LIBRARY_COMPOSITE
                ).get("version")
                or ""
            ).strip()
            or MODEL_CONFIG_DEFAULT
        )
        timestamp = now_ms()
        progress = {
            "step": "prepare",
            "label": "正在冻结综合分析上游",
            "completed": 0,
            "total": TOTAL_STEPS,
            "started_at": timestamp,
            "updated_at": timestamp,
            "elapsed_ms": 0,
        }
        upstream_refs = {
            f"{scheme}_run_id": str(run["analysis_run_id"])
            for scheme, run in upstream.items()
        }
        upstream_refs.update(
            {
                f"{scheme}_result_hash": str(run["result_hash"])
                for scheme, run in upstream.items()
            }
        )
        business_run = self.run_repo.create_queued(
            asset_id=asset_id,
            scheme="composite",
            timestamp=timestamp,
            progress=progress,
            prompt_version=(
                str(prompt_version or "").strip()
                or PROMPT_VERSION_DEFAULT
            ),
            model_config_id=model_config_id,
            upstream_refs=upstream_refs,
        )
        analysis_run_id = str(business_run["analysis_run_id"])
        threading.Thread(
            target=self._run,
            kwargs={
                "asset": asset,
                "task": task,
                "session": session,
                "upstream": upstream,
                "analysis_run_id": analysis_run_id,
                "started_at": timestamp,
                "prompt_version": str(
                    business_run["prompt_version"]
                ),
                "model_config_id": model_config_id,
            },
            name=f"open-cut-composite-{task['id']}",
            daemon=True,
        ).start()
        return {
            "status": "queued",
            "analysis_run_id": analysis_run_id,
        }

    def _snapshot_input(
        self,
        *,
        workspace: Path,
        session_id: int,
        run: dict[str, Any],
    ) -> str:
        relative = str(run.get("result_index_path") or "")
        prefix = f"tool_use_sessions/{run.get('tool_use_session_id')}/"
        path = (workspace / relative).resolve()
        if (
            not relative.startswith(prefix)
            or not path.is_relative_to(workspace)
            or not path.is_file()
            or self.ctx.session_repo.get_file(session_id, relative) is None
        ):
            raise ValueError("composite_upstream_result_untrusted")
        payload = _read_object(path)
        if result_hash(payload) != str(run.get("result_hash") or ""):
            raise ValueError("composite_upstream_result_hash_mismatch")
        return relative

    def _run(
        self,
        *,
        asset: dict[str, Any],
        task: dict[str, Any],
        session: dict[str, Any],
        upstream: dict[str, dict[str, Any]],
        analysis_run_id: str,
        started_at: int,
        prompt_version: str,
        model_config_id: str,
    ) -> None:
        workspace = Path(str(session["workspace_dir"])).resolve()
        session_id = int(task["session_id"])
        runner: ToolSessionRunner | None = None
        tool_use_session_id = ""
        run_finalized = False
        terminal_status = "failed"
        try:
            source_paths = {
                scheme: self._snapshot_input(
                    workspace=workspace,
                    session_id=session_id,
                    run=run,
                )
                for scheme, run in upstream.items()
            }
            targets = {
                "dialogue": "composite_inputs/dialogue_fragment_index.json",
                "visual_structure": (
                    "composite_inputs/visual_structure_segments.json"
                ),
                "visual_semantic": (
                    "composite_inputs/visual_semantic_segments.json"
                ),
            }
            prepared = ToolSessionService(
                event_sink=self.ctx.session_event_service
            ).start(
                PrepareSessionVariablesInput(
                    workspace_dir=workspace,
                    workflow_id=WORKFLOW_ID,
                    task_id=int(task["id"]),
                    opencrew_session_id=session_id,
                    selected_scheme="composite",
                    input_files=[
                        PrepareInputFile(
                            source_path=source_paths[scheme],
                            target_name=targets[scheme],
                            visibility="internal",
                        )
                        for scheme in (
                            "dialogue",
                            "visual_structure",
                            "visual_semantic",
                        )
                    ],
                ),
                session_id=session_id,
            )
            tool_use_session_id = prepared.tool_use_session_id
            tool_root = (
                workspace / "tool_use_sessions" / tool_use_session_id
            )
            runner = ToolSessionRunner(
                workspace_dir=workspace,
                tool_use_session_id=tool_use_session_id,
                session_id=session_id,
                event_sink=self.ctx.session_event_service,
            )
            _write_json(
                tool_root / INPUT_MANIFEST_REL,
                {
                    "schema_version": INPUT_SCHEMA_VERSION,
                    "asset_id": str(asset["asset_id"]),
                    "source_version": str(asset["content_sha256"]),
                    "dialogue_run_id": str(
                        upstream["dialogue"]["analysis_run_id"]
                    ),
                    "dialogue_result_hash": str(
                        upstream["dialogue"]["result_hash"]
                    ),
                    "visual_structure_run_id": str(
                        upstream["visual_structure"]["analysis_run_id"]
                    ),
                    "visual_structure_result_hash": str(
                        upstream["visual_structure"]["result_hash"]
                    ),
                    "visual_semantic_run_id": str(
                        upstream["visual_semantic"]["analysis_run_id"]
                    ),
                    "visual_semantic_result_hash": str(
                        upstream["visual_semantic"]["result_hash"]
                    ),
                    "sampling_strategy": "scene_uniform_4_v1",
                    "composite_prompt_version": prompt_version,
                    "model_config_id": model_config_id,
                    "source_duration_ms": int(asset["duration_ms"]),
                    "source_width": (
                        int(asset["width"])
                        if asset.get("width") is not None
                        else None
                    ),
                    "source_height": (
                        int(asset["height"])
                        if asset.get("height") is not None
                        else None
                    ),
                    "source_orientation": _orientation(
                        asset.get("width"), asset.get("height")
                    ),
                },
            )
            progress = {
                "step": TOOL_ID,
                "label": "正在生成综合语义索引",
                "completed": 1,
                "total": TOTAL_STEPS,
                "started_at": started_at,
                "updated_at": now_ms(),
                "elapsed_ms": max(0, now_ms() - started_at),
            }
            self.run_repo.mark_running(
                analysis_run_id,
                timestamp=now_ms(),
                tool_use_session_id=tool_use_session_id,
                progress=progress,
            )
            adapter = CompositeAnalysisToolAdapter(
                ctx=self.ctx,
                session=session,
                asset={**asset, "task_id": task["id"]},
                analysis_run_id=analysis_run_id,
                upstream=upstream,
                composite_prompt_version=prompt_version,
                model_config_id=model_config_id,
            )
            result = runner.run_registry_step(
                step_id=STEP_ID,
                tool_id=TOOL_ID,
                normalized_registry=normalize_registry_file(
                    OPEN_CUT_REGISTRY, strict=True
                ),
                step_index=1,
                adapters={TOOL_ID: adapter},
            )
            if result.status != "completed":
                terminal_status = (
                    "blocked" if result.status == "blocked" else "failed"
                )
                error = result.outputs.get("error")
                if isinstance(error, dict):
                    raise CompositeBlocked(
                        str(error.get("code") or "analysis_blocked"),
                        str(
                            error.get("user_message")
                            or "综合分析被阻止。"
                        ),
                        str(
                            error.get("suggested_action")
                            or "检查模型配置后重试。"
                        ),
                    )
                raise RuntimeError(
                    "; ".join(result.errors) or "综合分析工具执行失败。"
                )
            sync_result = finalize_analysis_tool_session(
                self.ctx, runner, terminal_status="completed"
            )
            run_finalized = True
            if sync_result.status != "completed":
                raise RuntimeError(
                    f"综合分析结果登记失败：{result_sync_error(sync_result)}"
                )
            published = _read_object(tool_root / RESULT_PATH)
            digest = result_hash(published)
            finished_at = now_ms()
            completed_progress = {
                "step": "completed",
                "label": "综合分析已完成",
                "completed": TOTAL_STEPS,
                "total": TOTAL_STEPS,
                "started_at": started_at,
                "updated_at": finished_at,
                "finished_at": finished_at,
                "elapsed_ms": max(0, finished_at - started_at),
            }
            fragments = [
                {
                    "fragment_id": item["fragment_id"],
                    "start_ms": item["start_ms"],
                    "end_ms": item["end_ms"],
                    "dialogue_text": item.get("dialogue_text"),
                    "title": item.get("title"),
                    "summary": item.get("summary"),
                    "keywords": item.get("keywords") or [],
                    "visual_labels": [
                        *item.get("people", []),
                        *item.get("objects", []),
                        *([item["scene"]] if item.get("scene") else []),
                    ],
                    "keyframe_ref": item.get("keyframe_refs") or [],
                    "confidence": item.get("confidence"),
                    "needs_review": bool(item.get("needs_review")),
                }
                for item in (published.get("items") or [])
            ]
            expected_upstream_refs = {
                scheme: {
                    "analysis_run_id": str(run["analysis_run_id"]),
                    "result_hash": str(run["result_hash"]),
                }
                for scheme, run in upstream.items()
            }
            self.ctx.media_library_fragment_publisher.publish(
                asset_id=str(asset["asset_id"]),
                analysis_run_id=analysis_run_id,
                analysis_scheme="composite",
                result_hash=digest,
                fragments=fragments,
                timestamp=finished_at,
                schema_version=str(published["schema_version"]),
                result_index_path=(
                    f"tool_use_sessions/{tool_use_session_id}/{RESULT_PATH}"
                ),
                progress=completed_progress,
                expected_upstream_refs=expected_upstream_refs,
            )
            self.ctx.session_event_service.add_event(
                session_id,
                "open_cut.composite.completed",
                {
                    "asset_id": str(asset["asset_id"]),
                    "analysis_run_id": analysis_run_id,
                    "tool_use_session_id": tool_use_session_id,
                    "result_hash": digest,
                    "fragment_count": len(fragments),
                },
                workflow_id=WORKFLOW_ID,
            )
        except Exception as exc:
            if isinstance(exc, CompositeBlocked):
                terminal_status = "blocked"
            elif (
                isinstance(exc, HTTPException)
                and isinstance(exc.detail, dict)
                and exc.detail.get("code") == "analysis_upstream_changed"
            ):
                terminal_status = "stale"
            sync_errors: list[str] = []
            if runner is not None and not run_finalized:
                try:
                    sync_result = finalize_analysis_tool_session(
                        self.ctx, runner, terminal_status=terminal_status
                    )
                    run_finalized = True
                    sync_errors = list(sync_result.errors)
                except Exception as sync_exc:
                    sync_errors = [
                        str(sync_exc).strip()
                        or sync_exc.__class__.__name__
                    ]
            finished_at = now_ms()
            if isinstance(exc, CompositeBlocked):
                error: dict[str, Any] = exc.payload()
            elif isinstance(exc, HTTPException) and isinstance(
                exc.detail, dict
            ):
                error = dict(exc.detail)
            else:
                error = {
                    "code": "composite_execution_failed",
                    "user_message": str(exc).strip()
                    or "综合分析失败。",
                    "suggested_action": "检查输入与模型服务后重新运行。",
                }
            error.update(
                {
                    "run_id": analysis_run_id,
                    "failed_step": TOOL_ID,
                    "result_sync_errors": sync_errors,
                }
            )
            failed_progress = {
                "step": terminal_status,
                "label": {
                    "blocked": "综合分析被阻止",
                    "stale": "综合分析上游已变化",
                }.get(terminal_status, "综合分析失败"),
                "completed": 0,
                "total": TOTAL_STEPS,
                "started_at": started_at,
                "updated_at": finished_at,
                "finished_at": finished_at,
                "elapsed_ms": max(0, finished_at - started_at),
            }
            self.run_repo.finish_unsuccessful(
                analysis_run_id,
                status=terminal_status,
                timestamp=finished_at,
                error_code=str(error["code"]),
                error=error,
                progress=failed_progress,
            )
            self.ctx.session_event_service.add_event(
                session_id,
                f"open_cut.composite.{terminal_status}",
                {
                    "asset_id": str(asset["asset_id"]),
                    "analysis_run_id": analysis_run_id,
                    "tool_use_session_id": tool_use_session_id,
                    "error": {
                        key: value
                        for key, value in error.items()
                        if key != "result_sync_errors"
                    },
                    "result_sync_errors": sync_errors,
                },
                workflow_id=WORKFLOW_ID,
            )


__all__ = [
    "CompositeAnalysisService",
    "CompositeAnalysisToolAdapter",
    "CompositeBlocked",
    "CompositeModelOutputError",
    "PROMPT_VERSION_DEFAULT",
]
