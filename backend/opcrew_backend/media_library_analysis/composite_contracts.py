from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contracts import _read_object, _write_json, result_hash


INPUT_SCHEMA_VERSION = "media_library_composite_input_v1"
CANDIDATE_SCHEMA_VERSION = "media_library_composite_candidate_v1"
RESULT_SCHEMA_VERSION = "media_library_composite_v1"
SAMPLING_STRATEGY = "scene_uniform_4_v1"

INPUT_ROOT_REL = "0_SessionContext/composite_inputs"
INPUT_MANIFEST_REL = f"{INPUT_ROOT_REL}/InputManifest.json"
INPUT_DIALOGUE_REL = f"{INPUT_ROOT_REL}/dialogue_fragment_index.json"
INPUT_STRUCTURE_REL = f"{INPUT_ROOT_REL}/visual_structure_segments.json"
INPUT_SEMANTIC_REL = f"{INPUT_ROOT_REL}/visual_semantic_segments.json"

RESULT_PATH = "SessionOutput/json/composite_semantic_segments.json"
INDEX_PATH = "SessionOutput/json/composite_fragment_index.jsonl"
VIRTUAL_CLIPS_PATH = (
    "SessionOutput/manifests/composite_virtual_clips.json"
)
SEARCH_MANIFEST_PATH = (
    "SessionOutput/manifests/search_index_manifest.json"
)
QUALITY_PATH = "SessionReport/composite_quality_check.json"

ITEM_KEYS = {
    "fragment_id",
    "asset_id",
    "scheme",
    "start_ms",
    "end_ms",
    "duration_ms",
    "title",
    "summary",
    "dialogue_text",
    "visual_summary",
    "keywords",
    "people",
    "objects",
    "scene",
    "action",
    "dialogue_refs",
    "visual_refs",
    "visual_claim_refs",
    "keyframe_refs",
    "boundary_reasons",
    "confidence",
    "needs_review",
}
SENSITIVE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:真实身份|真实姓名|姓名是|名叫|种族|族裔|政治立场|宗教信仰|性取向|确诊|患有)",
        r"\b(?:identified|recognized|real name|race|ethnicity|political affiliation|religious belief|sexual orientation|diagnosed with)\b",
    )
)


class CompositeValidationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


def _fail(code: str, detail: str = "") -> None:
    raise CompositeValidationError(code, detail)


def _object(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(code)
    return dict(value)


def _text(
    value: Any,
    code: str,
    *,
    maximum: int,
    nullable: bool = False,
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        _fail(code)
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        _fail(code)
    return normalized


def _texts(
    value: Any,
    code: str,
    *,
    maximum_items: int = 64,
    maximum_length: int = 256,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        _fail(code)
    result = [
        str(_text(item, code, maximum=maximum_length))
        for item in value
    ]
    if len(set(result)) != len(result):
        _fail(code)
    return result


def _integer(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(code)
    return value


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("composite_confidence_invalid")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        _fail("composite_confidence_invalid")
    return number


def _unique_join(values: list[str]) -> str | None:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return " ".join(unique) if unique else None


def _reject_sensitive(values: list[str]) -> None:
    for value in values:
        if any(pattern.search(value) for pattern in SENSITIVE_PATTERNS):
            _fail("composite_identity_or_sensitive_inference_forbidden")


def _validate_snapshot(
    *,
    tool_root: Path,
    asset_id: str,
    source_version: str,
    dialogue_run_id: str,
    dialogue_result_hash: str,
    visual_structure_run_id: str,
    visual_structure_result_hash: str,
    visual_semantic_run_id: str,
    visual_semantic_result_hash: str,
    composite_prompt_version: str,
    model_config_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], int]:
    manifest = _read_object(tool_root / INPUT_MANIFEST_REL)
    dialogue = _read_object(tool_root / INPUT_DIALOGUE_REL)
    structure = _read_object(tool_root / INPUT_STRUCTURE_REL)
    semantic = _read_object(tool_root / INPUT_SEMANTIC_REL)
    expected = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "asset_id": asset_id,
        "source_version": source_version,
        "dialogue_run_id": dialogue_run_id,
        "dialogue_result_hash": dialogue_result_hash,
        "visual_structure_run_id": visual_structure_run_id,
        "visual_structure_result_hash": visual_structure_result_hash,
        "visual_semantic_run_id": visual_semantic_run_id,
        "visual_semantic_result_hash": visual_semantic_result_hash,
        "sampling_strategy": SAMPLING_STRATEGY,
        "composite_prompt_version": composite_prompt_version,
        "model_config_id": model_config_id,
    }
    if set(manifest) != {
        *expected,
        "source_duration_ms",
        "source_width",
        "source_height",
        "source_orientation",
    }:
        _fail("composite_input_manifest_fields_invalid")
    for field, value in expected.items():
        if manifest.get(field) != value:
            _fail("composite_input_manifest_mismatch", field)
    duration_ms = _integer(
        manifest.get("source_duration_ms"),
        "composite_source_duration_invalid",
    )
    if duration_ms <= 0:
        _fail("composite_source_duration_invalid")
    expected_payloads = (
        (
            dialogue,
            "media_library_dialogue_fragments_v1",
            dialogue_run_id,
            dialogue_result_hash,
        ),
        (
            structure,
            "media_library_visual_structure_v2",
            visual_structure_run_id,
            visual_structure_result_hash,
        ),
        (
            semantic,
            "media_library_visual_semantic_v2",
            visual_semantic_run_id,
            visual_semantic_result_hash,
        ),
    )
    for payload, schema_version, run_id, digest in expected_payloads:
        if (
            payload.get("schema_version") != schema_version
            or payload.get("asset_id") != asset_id
            or payload.get("source_version") != source_version
            or payload.get("analysis_run_id") != run_id
            or result_hash(payload) != digest
        ):
            _fail("composite_upstream_snapshot_invalid", schema_version)
    if (
        structure.get("sampling_strategy") != SAMPLING_STRATEGY
        or semantic.get("sampling_strategy") != SAMPLING_STRATEGY
        or semantic.get("visual_structure_run_id")
        != visual_structure_run_id
        or semantic.get("visual_structure_result_hash")
        != visual_structure_result_hash
    ):
        _fail("composite_visual_upstream_mismatch")
    return dialogue, structure, semantic, duration_ms


def validate_composite_candidate(
    *,
    asset_id: str,
    dialogue: Mapping[str, Any],
    structure: Mapping[str, Any],
    semantic: Mapping[str, Any],
    source_duration_ms: int,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    value = _object(candidate, "composite_candidate_invalid")
    if set(value) != {"schema_version", "items"}:
        _fail("composite_candidate_fields_invalid")
    if value.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        _fail("composite_candidate_schema_invalid")
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        _fail("composite_fragments_empty")
    dialogue_by_id = {
        str(item.get("fragment_id") or ""): dict(item)
        for item in (dialogue.get("items") or [])
        if isinstance(item, dict)
    }
    structure_by_id = {
        str(item.get("fragment_id") or ""): dict(item)
        for item in (structure.get("items") or [])
        if isinstance(item, dict)
    }
    semantic_by_id = {
        str(item.get("fragment_id") or ""): dict(item)
        for item in (semantic.get("items") or [])
        if isinstance(item, dict)
    }
    if set(structure_by_id) != set(semantic_by_id):
        _fail("composite_visual_fragment_set_mismatch")
    allowed_boundaries = {0, source_duration_ms}
    for upstream in (*dialogue_by_id.values(), *semantic_by_id.values()):
        allowed_boundaries.add(int(upstream.get("start_ms") or 0))
        allowed_boundaries.add(int(upstream.get("end_ms") or 0))

    normalized: list[dict[str, Any]] = []
    previous_end = -1
    for index, raw in enumerate(raw_items, start=1):
        item = _object(raw, "composite_item_invalid")
        if set(item) != ITEM_KEYS:
            _fail("composite_item_fields_invalid", str(index))
        fragment_id = f"composite_{index:04d}"
        if item.get("fragment_id") != fragment_id:
            _fail("composite_fragment_id_modified", fragment_id)
        if item.get("asset_id") != asset_id or item.get("scheme") != "composite":
            _fail("composite_identity_modified", fragment_id)
        start_ms = _integer(item.get("start_ms"), "composite_time_invalid")
        end_ms = _integer(item.get("end_ms"), "composite_time_invalid")
        if (
            start_ms not in allowed_boundaries
            or end_ms not in allowed_boundaries
            or start_ms < 0
            or end_ms <= start_ms
            or end_ms > source_duration_ms
            or start_ms < previous_end
        ):
            _fail("composite_time_invalid", fragment_id)
        if item.get("duration_ms") != end_ms - start_ms:
            _fail("composite_duration_invalid", fragment_id)
        previous_end = end_ms
        dialogue_refs = _texts(
            item.get("dialogue_refs"),
            "composite_dialogue_refs_invalid",
            maximum_length=128,
        )
        visual_refs = _texts(
            item.get("visual_refs"),
            "composite_visual_refs_invalid",
            maximum_length=128,
        )
        if not dialogue_refs and not visual_refs:
            _fail("composite_evidence_refs_empty", fragment_id)
        if not set(dialogue_refs).issubset(dialogue_by_id):
            _fail("composite_dialogue_ref_unknown", fragment_id)
        if not set(visual_refs).issubset(semantic_by_id):
            _fail("composite_visual_ref_unknown", fragment_id)
        for ref in (*dialogue_refs, *visual_refs):
            upstream = dialogue_by_id.get(ref) or semantic_by_id.get(ref)
            if (
                int(upstream["start_ms"]) < start_ms
                or int(upstream["end_ms"]) > end_ms
            ):
                _fail("composite_ref_outside_boundary", ref)

        expected_dialogue = _unique_join(
            [
                str(dialogue_by_id[ref].get("dialogue_text") or "")
                for ref in dialogue_refs
            ]
        )
        dialogue_text = _text(
            item.get("dialogue_text"),
            "composite_dialogue_text_modified",
            maximum=6000,
            nullable=True,
        )
        if dialogue_text != expected_dialogue:
            _fail("composite_dialogue_text_modified", fragment_id)
        expected_visual_summary = _unique_join(
            [
                str(semantic_by_id[ref].get("visual_summary") or "")
                for ref in visual_refs
            ]
        )
        visual_summary = _text(
            item.get("visual_summary"),
            "composite_visual_summary_modified",
            maximum=6000,
            nullable=True,
        )
        if visual_summary != expected_visual_summary:
            _fail("composite_visual_summary_modified", fragment_id)

        people = _texts(
            item.get("people"), "composite_people_invalid"
        )
        objects = _texts(
            item.get("objects"), "composite_objects_invalid"
        )
        scene = _text(
            item.get("scene"),
            "composite_scene_invalid",
            maximum=256,
            nullable=True,
        )
        if item.get("action") is not None:
            _fail("composite_midpoint_action_must_be_null", fragment_id)
        claims: dict[str, Any] = {
            "people": people,
            "objects": objects,
            "scene": scene,
            "action": None,
        }
        claim_refs_raw = _object(
            item.get("visual_claim_refs"),
            "composite_visual_claim_refs_invalid",
        )
        if set(claim_refs_raw) != set(claims):
            _fail("composite_visual_claim_refs_invalid", fragment_id)
        claim_refs = {
            field: _texts(
                claim_refs_raw.get(field),
                "composite_visual_claim_refs_invalid",
                maximum_length=128,
            )
            for field in claims
        }
        for field, claim in claims.items():
            refs = claim_refs[field]
            if not set(refs).issubset(visual_refs):
                _fail("composite_visual_claim_ref_unknown", field)
            if bool(claim) != bool(refs):
                _fail("composite_visual_claim_evidence_required", field)
            values = claim if isinstance(claim, list) else [claim]
            for claim_value in [value for value in values if value]:
                if not any(
                    (
                        claim_value
                        in (
                            semantic_by_id[ref].get(field)
                            if isinstance(
                                semantic_by_id[ref].get(field), list
                            )
                            else [semantic_by_id[ref].get(field)]
                        )
                    )
                    for ref in refs
                ):
                    _fail("composite_visual_fact_unsupported", field)
        if claim_refs["action"]:
            _fail("composite_midpoint_action_must_be_null", fragment_id)

        expected_keyframes: list[str] = []
        for ref in visual_refs:
            for keyframe_ref in (
                semantic_by_id[ref].get("keyframe_refs") or []
            ):
                keyframe_ref = str(keyframe_ref)
                if keyframe_ref not in expected_keyframes:
                    expected_keyframes.append(keyframe_ref)
        if item.get("keyframe_refs") != expected_keyframes:
            _fail("composite_keyframe_refs_modified", fragment_id)
        title = str(
            _text(
                item.get("title"),
                "composite_title_invalid",
                maximum=200,
            )
        )
        summary = str(
            _text(
                item.get("summary"),
                "composite_summary_invalid",
                maximum=1500,
            )
        )
        keywords = _texts(
            item.get("keywords"),
            "composite_keywords_invalid",
            maximum_items=32,
            maximum_length=128,
        )
        boundary_reasons = _texts(
            item.get("boundary_reasons"),
            "composite_boundary_reasons_invalid",
            maximum_items=16,
            maximum_length=256,
        )
        _reject_sensitive(
            [
                title,
                summary,
                *keywords,
                *people,
                *objects,
                *([scene] if scene else []),
            ]
        )
        needs_review = item.get("needs_review")
        if not isinstance(needs_review, bool):
            _fail("composite_needs_review_invalid")
        normalized.append(
            {
                "fragment_id": fragment_id,
                "asset_id": asset_id,
                "scheme": "composite",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_ms": end_ms - start_ms,
                "title": title,
                "summary": summary,
                "dialogue_text": dialogue_text,
                "visual_summary": visual_summary,
                "keywords": keywords,
                "people": people,
                "objects": objects,
                "scene": scene,
                "action": None,
                "dialogue_refs": dialogue_refs,
                "visual_refs": visual_refs,
                "visual_claim_refs": claim_refs,
                "keyframe_refs": expected_keyframes,
                "boundary_reasons": boundary_reasons,
                "confidence": _confidence(item.get("confidence")),
                "needs_review": needs_review,
            }
        )
    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "items": normalized,
    }


def publish_composite_contract(
    *,
    tool_root: Path,
    asset_id: str,
    source_version: str,
    analysis_run_id: str,
    dialogue_run_id: str,
    dialogue_result_hash: str,
    visual_structure_run_id: str,
    visual_structure_result_hash: str,
    visual_semantic_run_id: str,
    visual_semantic_result_hash: str,
    composite_prompt_version: str,
    model_config_id: str,
    candidate: Mapping[str, Any],
    write: bool = True,
) -> tuple[dict[str, Any], str, str]:
    dialogue, structure, semantic, duration_ms = _validate_snapshot(
        tool_root=tool_root,
        asset_id=asset_id,
        source_version=source_version,
        dialogue_run_id=dialogue_run_id,
        dialogue_result_hash=dialogue_result_hash,
        visual_structure_run_id=visual_structure_run_id,
        visual_structure_result_hash=visual_structure_result_hash,
        visual_semantic_run_id=visual_semantic_run_id,
        visual_semantic_result_hash=visual_semantic_result_hash,
        composite_prompt_version=composite_prompt_version,
        model_config_id=model_config_id,
    )
    normalized = validate_composite_candidate(
        asset_id=asset_id,
        dialogue=dialogue,
        structure=structure,
        semantic=semantic,
        source_duration_ms=duration_ms,
        candidate=candidate,
    )
    payload = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "asset_id": asset_id,
        "source_version": source_version,
        "analysis_run_id": analysis_run_id,
        "dialogue_run_id": dialogue_run_id,
        "dialogue_result_hash": dialogue_result_hash,
        "visual_structure_run_id": visual_structure_run_id,
        "visual_structure_result_hash": visual_structure_result_hash,
        "visual_semantic_run_id": visual_semantic_run_id,
        "visual_semantic_result_hash": visual_semantic_result_hash,
        "sampling_strategy": SAMPLING_STRATEGY,
        "composite_prompt_version": composite_prompt_version,
        "model_config_id": model_config_id,
        "items": normalized["items"],
    }
    digest = result_hash(payload)
    if write:
        _write_json(tool_root / RESULT_PATH, payload)
        index_path = tool_root / INDEX_PATH
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            "".join(
                json.dumps(
                    {
                        **item,
                        "search_text": " ".join(
                            str(value or "")
                            for value in (
                                item["dialogue_text"],
                                item["title"],
                                item["summary"],
                                *item["keywords"],
                                *item["people"],
                                *item["objects"],
                                item["scene"],
                            )
                            if value
                        ),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
                for item in payload["items"]
            ),
            encoding="utf-8",
        )
        _write_json(
            tool_root / VIRTUAL_CLIPS_PATH,
            {
                "schema_version": "media_library_composite_virtual_clips_v1",
                "asset_id": asset_id,
                "analysis_run_id": analysis_run_id,
                "result_hash": digest,
                "items": [
                    {
                        "fragment_id": item["fragment_id"],
                        "start_ms": item["start_ms"],
                        "end_ms": item["end_ms"],
                        "default_clip_eligible": True,
                    }
                    for item in payload["items"]
                ],
            },
        )
        _write_json(
            tool_root / SEARCH_MANIFEST_PATH,
            {
                "schema_version": "media_library_search_index_manifest_v1",
                "asset_id": asset_id,
                "analysis_run_id": analysis_run_id,
                "analysis_scheme": "composite",
                "result_hash": digest,
                "result_path": RESULT_PATH,
                "index_path": INDEX_PATH,
                "fragment_count": len(payload["items"]),
            },
        )
        _write_json(
            tool_root / QUALITY_PATH,
            {
                "schema_version": "media_library_composite_quality_v1",
                "valid": True,
                "fragment_count": len(payload["items"]),
                "result_hash": digest,
                "unsupported_visual_claim_count": 0,
                "sampling_strategy": SAMPLING_STRATEGY,
                "action_claim_count": 0,
            },
        )
    return payload, digest, RESULT_PATH


__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "CompositeValidationError",
    "INDEX_PATH",
    "INPUT_DIALOGUE_REL",
    "INPUT_MANIFEST_REL",
    "INPUT_SCHEMA_VERSION",
    "INPUT_SEMANTIC_REL",
    "INPUT_STRUCTURE_REL",
    "QUALITY_PATH",
    "RESULT_PATH",
    "RESULT_SCHEMA_VERSION",
    "SEARCH_MANIFEST_PATH",
    "VIRTUAL_CLIPS_PATH",
    "publish_composite_contract",
    "validate_composite_candidate",
]
