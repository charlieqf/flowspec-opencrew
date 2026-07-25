from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Callable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .contracts import _read_object, _write_json, result_hash, sha256_file


SAMPLING_STRATEGY = "scene_uniform_4_v1"
INPUT_SCHEMA_VERSION = "media_library_visual_semantic_input_v2"
CANDIDATE_SCHEMA_VERSION = "media_library_visual_semantic_candidate_v2"
RESULT_SCHEMA_VERSION = "media_library_visual_semantic_v2"
STRUCTURE_SCHEMA_VERSION = "media_library_visual_structure_v2"
STRUCTURE_MANIFEST_SCHEMA_VERSION = (
    "media_library_visual_structure_manifest_v2"
)
KEYFRAMES_PER_FRAGMENT = 4

_SCHEMA_ROOT = (
    Path(__file__).resolve().parents[3]
    / "ToolLibrary"
    / "OpenCut_V1"
    / "schemas"
)

INPUT_ROOT_REL = "0_SessionContext/visual_inputs"
INPUT_MANIFEST_REL = f"{INPUT_ROOT_REL}/visual_semantic_input_manifest.json"
INPUT_STRUCTURE_MANIFEST_REL = f"{INPUT_ROOT_REL}/visual_structure_manifest.json"
INPUT_STRUCTURE_SEGMENTS_REL = f"{INPUT_ROOT_REL}/visual_structure_segments.json"
INPUT_KEYFRAMES_REL = f"{INPUT_ROOT_REL}/keyframes"

RESULT_PATH = "SessionOutput/visual/visual_semantic_segments.json"
MANIFEST_PATH = "SessionOutput/visual/visual_semantic_manifest.json"
QUALITY_PATH = "SessionReport/visual_semantic_quality_check.json"

_ITEM_KEYS = {
    "fragment_id",
    "start_ms",
    "end_ms",
    "duration_ms",
    "keyframe_refs",
    "visual_summary",
    "people",
    "objects",
    "scene",
    "action",
    "keywords",
    "claim_evidence",
    "confidence",
    "needs_review",
}
_ITEM_REQUIRED_KEYS = _ITEM_KEYS - {"duration_ms"}
_CANDIDATE_KEYS = {
    "schema_version",
    "asset_id",
    "source_version",
    "visual_structure_run_id",
    "visual_structure_result_hash",
    "sampling_strategy",
    "items",
}
_INPUT_MANIFEST_KEYS = {
    "schema_version",
    "asset_id",
    "source_version",
    "visual_structure_run_id",
    "visual_structure_result_hash",
    "sampling_strategy",
    "keyframes",
    "visual_prompt_version",
    "model_config_id",
    "allow_cloud_visual_data_transfer",
}
_EVIDENCE_FIELDS = ("people", "objects", "scene", "action")
_IDENTITY_OR_SENSITIVE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:identified|recognised|recognized)\s+as\b",
        r"\b(?:real\s+name|legal\s+name|name\s+is)\b",
        r"(?:真实身份|真实姓名|姓名是|名叫|认出(?:是|为)|身份是)",
        r"(?:种族|族裔|民族是|亚裔|非裔|白人|黑人|拉丁裔)",
        r"\b(?:race|racial|ethnicity|ethnic|asian|african[- ]american|caucasian|latino)\b",
        r"(?:患有|确诊|疾病患者|残疾人|精神疾病|艾滋病|癌症患者)",
        r"\b(?:diagnosed\s+with|suffers?\s+from|has\s+(?:cancer|hiv|aids)|disabled\s+person)\b",
        r"(?:政治立场|政治倾向|党员|民主党人|共和党人|支持.{0,8}(?:党|候选人))",
        r"\b(?:political\s+affiliation|democrat|republican|party\s+member|supports?\s+(?:the\s+)?candidate)\b",
        r"(?:宗教信仰|佛教徒|基督徒|穆斯林|犹太教徒|印度教徒)",
        r"\b(?:religious\s+belief|buddhist|christian|muslim|jewish|hindu)\b",
        r"(?:性取向|同性恋|异性恋|双性恋|跨性别)",
        r"\b(?:sexual\s+orientation|gay|lesbian|bisexual|transgender)\b",
    )
)


class VisualSemanticValidationError(ValueError):
    """A stable, machine-readable visual-semantic contract failure."""

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


def _fail(code: str, *, detail: str | None = None) -> None:
    raise VisualSemanticValidationError(code, detail=detail)


@lru_cache(maxsize=1)
def _source_schema_validators() -> dict[str, Draft202012Validator]:
    """Load the checked-in v2 schemas used by the runtime publisher."""

    def load(name: str) -> dict[str, Any]:
        payload = json.loads(
            (_SCHEMA_ROOT / name).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(payload)
        return payload

    candidate = load("visual_semantic_candidate_v2.schema.json")
    input_manifest = load("visual_semantic_input_v2.schema.json")
    segments = load("visual_semantic_segments_v2.schema.json")
    # Keep the checked-in relative reference authoritative while constructing
    # a self-contained validator that never resolves a network URL.
    resolved_segments = copy.deepcopy(segments)
    resolved_segments["$defs"] = copy.deepcopy(candidate["$defs"])
    resolved_segments["properties"]["items"]["items"]["$ref"] = (
        "#/$defs/item"
    )
    Draft202012Validator.check_schema(resolved_segments)
    return {
        "candidate": Draft202012Validator(candidate),
        "input": Draft202012Validator(input_manifest),
        "segments": Draft202012Validator(resolved_segments),
    }


def _validate_source_schema(
    kind: str,
    payload: Mapping[str, Any],
    *,
    code: str,
) -> None:
    errors = sorted(
        _source_schema_validators()[kind].iter_errors(dict(payload)),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(part) for part in first.absolute_path)
    _fail(
        code,
        detail=f"{location or '$'}:{first.message}"[:500],
    )


def _require_object(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(code)
    return value


def _require_string(value: Any, code: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        _fail(code)
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        _fail(code)
    return normalized


def _optional_string(value: Any, code: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _require_string(value, code, maximum=maximum)


def _string_list(
    value: Any,
    code: str,
    *,
    maximum_items: int = 32,
    maximum_length: int = 128,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        _fail(code)
    normalized: list[str] = []
    for item in value:
        normalized.append(
            _require_string(item, code, maximum=maximum_length)
        )
    if len(set(normalized)) != len(normalized):
        _fail(code)
    return normalized


def _integer(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(code)
    return value


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("visual_semantic_confidence_invalid")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 1:
        _fail("visual_semantic_confidence_invalid")
    return number


def _reject_identity_or_sensitive_inference(values: list[str]) -> None:
    for value in values:
        for pattern in _IDENTITY_OR_SENSITIVE_PATTERNS:
            if pattern.search(value):
                _fail(
                    "visual_semantic_identity_or_sensitive_inference_forbidden",
                    detail=pattern.pattern,
                )


def _reject_person_identity_labels(people: list[str]) -> None:
    placeholder_names = {
        "张三",
        "李四",
        "王五",
        "赵六",
        "john doe",
        "jane doe",
    }
    proper_name = re.compile(
        r"^[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){1,3}$"
    )
    for person in people:
        if person.casefold() in placeholder_names or proper_name.fullmatch(person):
            _fail(
                "visual_semantic_identity_or_sensitive_inference_forbidden",
                detail="person_identity_label",
            )


def _authoritative_item_fields(
    authoritative_item: Mapping[str, Any],
    *,
    sampling_strategy: str,
) -> tuple[str, int, int, int, list[str]]:
    fragment_id = _require_string(
        authoritative_item.get("fragment_id"),
        "visual_structure_fragment_id_invalid",
        maximum=128,
    )
    start_ms = _integer(
        authoritative_item.get("start_ms"),
        "visual_structure_time_invalid",
    )
    end_ms = _integer(
        authoritative_item.get("end_ms"),
        "visual_structure_time_invalid",
    )
    if start_ms < 0 or end_ms <= start_ms:
        _fail("visual_structure_time_invalid", detail=fragment_id)
    duration_ms = end_ms - start_ms
    if authoritative_item.get("duration_ms") != duration_ms:
        _fail("visual_structure_duration_invalid", detail=fragment_id)
    if authoritative_item.get("sampling_strategy") != sampling_strategy:
        _fail("visual_structure_sampling_strategy_mismatch", detail=fragment_id)
    keyframes = authoritative_item.get("keyframes")
    if (
        not isinstance(keyframes, list)
        or len(keyframes) != KEYFRAMES_PER_FRAGMENT
    ):
        _fail("visual_structure_four_keyframes_required", detail=fragment_id)
    keyframe_refs: list[str] = []
    for slot_index, raw_keyframe in enumerate(keyframes):
        keyframe = _require_object(
            raw_keyframe,
            "visual_structure_keyframe_invalid",
        )
        expected_id = f"{fragment_id}-sample-{slot_index + 1:02d}"
        keyframe_id = _require_string(
            keyframe.get("keyframe_id"),
            "visual_structure_keyframe_id_invalid",
            maximum=128,
        )
        if keyframe_id != expected_id:
            _fail(
                "visual_structure_keyframe_id_invalid",
                detail=keyframe_id,
            )
        keyframe_time_ms = _integer(
            keyframe.get("keyframe_time_ms"),
            "visual_structure_keyframe_time_invalid",
        )
        if not start_ms <= keyframe_time_ms < end_ms:
            _fail(
                "visual_structure_keyframe_time_invalid",
                detail=keyframe_id,
            )
        image_sha256 = str(keyframe.get("image_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", image_sha256):
            _fail(
                "visual_structure_keyframe_hash_invalid",
                detail=keyframe_id,
            )
        image_path = str(keyframe.get("image_path") or "")
        if (
            not image_path
            or Path(image_path).is_absolute()
            or ".." in Path(image_path).parts
        ):
            _fail(
                "visual_structure_keyframe_path_invalid",
                detail=keyframe_id,
            )
        keyframe_refs.append(keyframe_id)
    return fragment_id, start_ms, end_ms, duration_ms, keyframe_refs


def validate_visual_semantic_item(
    *,
    authoritative_item: Mapping[str, Any],
    candidate_item: Mapping[str, Any],
    sampling_strategy: str = SAMPLING_STRATEGY,
) -> dict[str, Any]:
    """Validate and normalize one already-merged model candidate.

    Identifier, timing, and keyframe fields are copied from the authoritative
    visual-structure item after an exact equality check. A model response never
    becomes authoritative for those fields.
    """

    if sampling_strategy != SAMPLING_STRATEGY:
        _fail("visual_semantic_sampling_strategy_unsupported")
    authoritative = _require_object(
        dict(authoritative_item),
        "visual_structure_item_invalid",
    )
    candidate = _require_object(
        dict(candidate_item),
        "visual_semantic_item_invalid",
    )
    unknown = set(candidate) - _ITEM_KEYS
    if unknown:
        _fail(
            "visual_semantic_item_unknown_field",
            detail=",".join(sorted(unknown)),
        )
    missing = _ITEM_REQUIRED_KEYS - set(candidate)
    if missing:
        _fail(
            "visual_semantic_item_missing_field",
            detail=",".join(sorted(missing)),
        )
    fragment_id, start_ms, end_ms, duration_ms, keyframe_refs = (
        _authoritative_item_fields(
            authoritative,
            sampling_strategy=sampling_strategy,
        )
    )
    if candidate.get("fragment_id") != fragment_id:
        _fail("visual_semantic_fragment_id_modified", detail=fragment_id)
    if candidate.get("start_ms") != start_ms or candidate.get("end_ms") != end_ms:
        _fail("visual_semantic_time_modified", detail=fragment_id)
    if (
        "duration_ms" in candidate
        and candidate.get("duration_ms") != duration_ms
    ):
        _fail("visual_semantic_time_modified", detail=fragment_id)
    if candidate.get("keyframe_refs") != keyframe_refs:
        _fail("visual_semantic_keyframe_refs_modified", detail=fragment_id)

    visual_summary = _optional_string(
        candidate.get("visual_summary"),
        "visual_semantic_summary_invalid",
        maximum=1000,
    )
    people = _string_list(
        candidate.get("people"),
        "visual_semantic_people_invalid",
    )
    objects = _string_list(
        candidate.get("objects"),
        "visual_semantic_objects_invalid",
    )
    scene = _optional_string(
        candidate.get("scene"),
        "visual_semantic_scene_invalid",
        maximum=256,
    )
    action = candidate.get("action")
    if action is not None:
        _fail("visual_semantic_action_must_be_null")
    keywords = _string_list(
        candidate.get("keywords"),
        "visual_semantic_keywords_invalid",
    )
    evidence_raw = _require_object(
        candidate.get("claim_evidence"),
        "visual_semantic_claim_evidence_invalid",
    )
    if set(evidence_raw) != set(_EVIDENCE_FIELDS):
        _fail("visual_semantic_claim_evidence_invalid")
    evidence = {
        field: _string_list(
            evidence_raw.get(field),
            "visual_semantic_claim_evidence_invalid",
            maximum_items=KEYFRAMES_PER_FRAGMENT,
        )
        for field in _EVIDENCE_FIELDS
    }
    known_keyframes = set(keyframe_refs)
    for field, references in evidence.items():
        if not set(references).issubset(known_keyframes):
            _fail(
                "visual_semantic_unknown_keyframe_evidence",
                detail=f"{fragment_id}:{field}",
            )
    claims: dict[str, Any] = {
        "people": people,
        "objects": objects,
        "scene": scene,
        "action": action,
    }
    for field, claim in claims.items():
        non_empty = bool(claim)
        if non_empty and not evidence[field]:
            _fail(
                "visual_semantic_claim_evidence_required",
                detail=f"{fragment_id}:{field}",
            )
        if not non_empty and evidence[field]:
            _fail(
                "visual_semantic_claim_evidence_without_claim",
                detail=f"{fragment_id}:{field}",
            )
    if evidence["action"]:
        _fail("visual_semantic_action_must_be_null")

    sensitive_values = list(people) + list(objects) + list(keywords)
    sensitive_values.extend(
        value
        for value in (visual_summary, scene, action)
        if isinstance(value, str)
    )
    _reject_identity_or_sensitive_inference(sensitive_values)
    _reject_person_identity_labels(people)

    needs_review = candidate.get("needs_review")
    if not isinstance(needs_review, bool):
        _fail("visual_semantic_needs_review_invalid")
    return {
        "fragment_id": fragment_id,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "duration_ms": duration_ms,
        "keyframe_refs": keyframe_refs,
        "visual_summary": visual_summary,
        "people": people,
        "objects": objects,
        "scene": scene,
        "action": action,
        "keywords": keywords,
        "claim_evidence": evidence,
        "confidence": _confidence(candidate.get("confidence")),
        "needs_review": needs_review,
    }


def validate_visual_semantic_candidate(
    *,
    structure_segments: Mapping[str, Any],
    candidate: Mapping[str, Any],
    sampling_strategy: str = SAMPLING_STRATEGY,
) -> dict[str, Any]:
    """Validate a complete candidate and restore authoritative Scene order."""

    structure = _require_object(
        dict(structure_segments),
        "visual_structure_segments_invalid",
    )
    value = _require_object(
        dict(candidate),
        "visual_semantic_candidate_invalid",
    )
    unknown = set(value) - _CANDIDATE_KEYS
    if unknown:
        _fail(
            "visual_semantic_candidate_unknown_field",
            detail=",".join(sorted(unknown)),
        )
    if value.get("schema_version") not in (None, CANDIDATE_SCHEMA_VERSION):
        _fail("visual_semantic_candidate_schema_invalid")
    if structure.get("sampling_strategy") != sampling_strategy:
        _fail("visual_structure_sampling_strategy_mismatch")
    for field in ("asset_id", "source_version"):
        if field in value and value.get(field) != structure.get(field):
            _fail(f"visual_semantic_{field}_modified")
    if (
        "visual_structure_run_id" in value
        and value.get("visual_structure_run_id")
        != structure.get("analysis_run_id")
    ):
        _fail("visual_semantic_visual_structure_run_id_modified")
    if (
        "visual_structure_result_hash" in value
        and value.get("visual_structure_result_hash") != result_hash(structure)
    ):
        _fail("visual_semantic_visual_structure_result_hash_modified")
    if (
        "sampling_strategy" in value
        and value.get("sampling_strategy") != sampling_strategy
    ):
        _fail("visual_semantic_sampling_strategy_modified")

    raw_structure_items = structure.get("items")
    raw_candidate_items = value.get("items")
    if not isinstance(raw_structure_items, list) or not raw_structure_items:
        _fail("visual_structure_fragments_empty")
    if not isinstance(raw_candidate_items, list) or not raw_candidate_items:
        _fail("visual_semantic_fragments_empty")
    candidates_by_id: dict[str, dict[str, Any]] = {}
    for item in raw_candidate_items:
        candidate_item = _require_object(
            item,
            "visual_semantic_item_invalid",
        )
        fragment_id = str(candidate_item.get("fragment_id") or "")
        if not fragment_id or fragment_id in candidates_by_id:
            _fail("visual_semantic_fragment_id_duplicate_or_missing")
        candidates_by_id[fragment_id] = candidate_item
    normalized: list[dict[str, Any]] = []
    authoritative_ids: set[str] = set()
    for raw in raw_structure_items:
        authoritative = _require_object(
            raw,
            "visual_structure_item_invalid",
        )
        fragment_id = str(authoritative.get("fragment_id") or "")
        if not fragment_id or fragment_id in authoritative_ids:
            _fail("visual_structure_fragment_id_duplicate_or_missing")
        authoritative_ids.add(fragment_id)
        candidate_item = candidates_by_id.get(fragment_id)
        if candidate_item is None:
            _fail(
                "visual_semantic_fragment_missing",
                detail=fragment_id,
            )
        normalized.append(
            validate_visual_semantic_item(
                authoritative_item=authoritative,
                candidate_item=candidate_item,
                sampling_strategy=sampling_strategy,
            )
        )
    extra = set(candidates_by_id) - authoritative_ids
    if extra:
        _fail(
            "visual_semantic_fragment_unknown",
            detail=",".join(sorted(extra)),
        )
    normalized_candidate = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "items": normalized,
    }
    _validate_source_schema(
        "candidate",
        normalized_candidate,
        code="visual_semantic_candidate_source_schema_invalid",
    )
    return normalized_candidate


def validate_with_single_repair(
    candidate: Mapping[str, Any],
    validator: Callable[[Mapping[str, Any]], dict[str, Any]],
    repair: Callable[
        [dict[str, Any], VisualSemanticValidationError],
        Mapping[str, Any],
    ],
) -> tuple[dict[str, Any], bool]:
    """Run a pure validator, permit exactly one structured repair, then fail."""

    original = copy.deepcopy(dict(candidate))
    try:
        return validator(original), False
    except VisualSemanticValidationError as first_error:
        repaired = repair(copy.deepcopy(original), first_error)
        if not isinstance(repaired, Mapping):
            _fail("visual_semantic_structured_repair_invalid")
        try:
            return validator(copy.deepcopy(dict(repaired))), True
        except VisualSemanticValidationError as second_error:
            raise VisualSemanticValidationError(
                "visual_semantic_structured_repair_exhausted",
                detail=f"{first_error.code},{second_error.code}",
            ) from second_error


def validate_published_visual_semantic_result(
    payload: Mapping[str, Any],
    *,
    asset_id: str,
    source_version: str,
    analysis_run_id: str,
    expected_result_hash: str,
    source_duration_ms: int | None,
) -> dict[str, Any]:
    """Revalidate a durable v2 result before index-only backfill."""

    value = _require_object(
        copy.deepcopy(dict(payload)),
        "visual_semantic_result_invalid",
    )
    _validate_source_schema(
        "segments",
        value,
        code="visual_semantic_segments_source_schema_invalid",
    )
    expected = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "asset_id": asset_id,
        "source_version": source_version,
        "analysis_run_id": analysis_run_id,
        "sampling_strategy": SAMPLING_STRATEGY,
    }
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            _fail(
                "visual_semantic_backfill_identity_mismatch",
                detail=field,
            )
    if result_hash(value) != expected_result_hash:
        _fail("visual_semantic_backfill_result_hash_mismatch")
    items = value.get("items")
    if not isinstance(items, list) or not items:
        _fail("visual_semantic_fragments_empty")
    seen: set[str] = set()
    previous_end = -1
    for raw_item in items:
        item = _require_object(raw_item, "visual_semantic_item_invalid")
        fragment_id = _require_string(
            item.get("fragment_id"),
            "visual_semantic_fragment_id_duplicate_or_missing",
            maximum=128,
        )
        if fragment_id in seen:
            _fail("visual_semantic_fragment_id_duplicate_or_missing")
        seen.add(fragment_id)
        start_ms = _integer(
            item.get("start_ms"), "visual_semantic_time_invalid"
        )
        end_ms = _integer(
            item.get("end_ms"), "visual_semantic_time_invalid"
        )
        if (
            start_ms < 0
            or end_ms <= start_ms
            or start_ms < previous_end
            or item.get("duration_ms") != end_ms - start_ms
            or (
                source_duration_ms is not None
                and end_ms > source_duration_ms
            )
        ):
            _fail("visual_semantic_time_invalid", detail=fragment_id)
        previous_end = end_ms
        expected_keyframes = [
            f"{fragment_id}-sample-{index:02d}"
            for index in range(1, KEYFRAMES_PER_FRAGMENT + 1)
        ]
        if item.get("keyframe_refs") != expected_keyframes:
            _fail(
                "visual_semantic_four_keyframes_required",
                detail=fragment_id,
            )
    return value


def _snapshot_file(tool_root: Path, relative_path: str) -> Path:
    resolved_tool_root = tool_root.resolve()
    path = (resolved_tool_root / relative_path).resolve()
    if not path.is_relative_to(resolved_tool_root) or not path.is_file():
        _fail(
            "visual_semantic_input_snapshot_path_invalid",
            detail=relative_path,
        )
    return path


def _validate_input_snapshot(
    *,
    tool_root: Path,
    asset_id: str,
    source_version: str,
    current_visual_structure_run_id: str,
    current_visual_structure_result_hash: str,
    visual_prompt_version: str,
    model_config_id: str,
) -> tuple[dict[str, Any], int]:
    input_manifest = _read_object(
        _snapshot_file(tool_root, INPUT_MANIFEST_REL)
    )
    structure_manifest = _read_object(
        _snapshot_file(tool_root, INPUT_STRUCTURE_MANIFEST_REL)
    )
    structure_segments = _read_object(
        _snapshot_file(tool_root, INPUT_STRUCTURE_SEGMENTS_REL)
    )
    if set(input_manifest) != _INPUT_MANIFEST_KEYS:
        _fail("visual_semantic_input_manifest_fields_invalid")
    expected_input = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "asset_id": asset_id,
        "source_version": source_version,
        "visual_structure_run_id": current_visual_structure_run_id,
        "visual_structure_result_hash": current_visual_structure_result_hash,
        "sampling_strategy": SAMPLING_STRATEGY,
        "visual_prompt_version": visual_prompt_version,
        "model_config_id": model_config_id,
    }
    for field, expected in expected_input.items():
        if input_manifest.get(field) != expected:
            _fail(
                "visual_semantic_input_manifest_mismatch",
                detail=field,
            )
    if not isinstance(
        input_manifest.get("allow_cloud_visual_data_transfer"),
        bool,
    ):
        _fail("visual_semantic_input_manifest_authorization_invalid")
    expected_structure_manifest = {
        "schema_version": STRUCTURE_MANIFEST_SCHEMA_VERSION,
        "asset_id": asset_id,
        "source_version": source_version,
        "analysis_run_id": current_visual_structure_run_id,
        "result_hash": current_visual_structure_result_hash,
        "sampling_strategy": SAMPLING_STRATEGY,
    }
    for field, expected in expected_structure_manifest.items():
        if structure_manifest.get(field) != expected:
            _fail(
                "visual_semantic_structure_manifest_not_current",
                detail=field,
            )
    if result_hash(structure_segments) != current_visual_structure_result_hash:
        _fail("visual_semantic_structure_segments_hash_mismatch")
    expected_structure = {
        "schema_version": STRUCTURE_SCHEMA_VERSION,
        "asset_id": asset_id,
        "source_version": source_version,
        "analysis_run_id": current_visual_structure_run_id,
        "sampling_strategy": SAMPLING_STRATEGY,
    }
    for field, expected in expected_structure.items():
        if structure_segments.get(field) != expected:
            _fail(
                "visual_semantic_structure_segments_not_current",
                detail=field,
            )

    raw_keyframes = input_manifest.get("keyframes")
    if not isinstance(raw_keyframes, list) or not raw_keyframes:
        _fail("visual_semantic_input_keyframes_empty")
    frozen_keyframes: list[dict[str, str]] = []
    frozen_hashes: dict[str, str] = {}
    for raw in raw_keyframes:
        keyframe = _require_object(
            raw,
            "visual_semantic_input_keyframe_invalid",
        )
        keyframe_id = _require_string(
            keyframe.get("keyframe_id"),
            "visual_semantic_input_keyframe_invalid",
            maximum=128,
        )
        image_sha256 = str(keyframe.get("image_sha256") or "")
        if (
            keyframe_id in frozen_hashes
            or not re.fullmatch(r"[0-9a-f]{64}", image_sha256)
        ):
            _fail(
                "visual_semantic_input_keyframe_invalid",
                detail=keyframe_id,
            )
        frozen_hashes[keyframe_id] = image_sha256
        frozen_keyframes.append(
            {
                "keyframe_id": keyframe_id,
                "image_sha256": image_sha256,
            }
        )

    authoritative_keyframes: list[dict[str, str]] = []
    authoritative_hashes: dict[str, str] = {}
    for raw_item in structure_segments.get("items") or []:
        item = _require_object(
            raw_item,
            "visual_structure_item_invalid",
        )
        _authoritative_item_fields(
            item,
            sampling_strategy=SAMPLING_STRATEGY,
        )
        for keyframe in item["keyframes"]:
            keyframe_id = str(keyframe["keyframe_id"])
            if keyframe_id in authoritative_hashes:
                _fail(
                    "visual_structure_keyframe_id_duplicate",
                    detail=keyframe_id,
                )
            image_sha256 = str(keyframe["image_sha256"])
            authoritative_hashes[keyframe_id] = image_sha256
            authoritative_keyframes.append(
                {
                    "keyframe_id": keyframe_id,
                    "image_sha256": image_sha256,
                }
            )
    if frozen_keyframes != authoritative_keyframes:
        _fail("visual_semantic_input_keyframes_not_current")

    keyframe_root = (tool_root / INPUT_KEYFRAMES_REL).resolve()
    resolved_tool_root = tool_root.resolve()
    if (
        not keyframe_root.is_relative_to(resolved_tool_root)
        or not keyframe_root.is_dir()
    ):
        _fail("visual_semantic_input_keyframe_path_invalid")
    files_by_stem: dict[str, list[Path]] = {}
    for path in keyframe_root.iterdir():
        resolved = path.resolve()
        if (
            path.is_symlink()
            or not resolved.is_relative_to(keyframe_root)
            or not resolved.is_file()
        ):
            _fail(
                "visual_semantic_input_keyframe_path_invalid",
                detail=path.name,
            )
        files_by_stem.setdefault(path.stem, []).append(resolved)
    if set(files_by_stem) != set(frozen_hashes):
        _fail("visual_semantic_input_keyframe_files_not_current")
    for keyframe_id, expected_hash in frozen_hashes.items():
        matches = files_by_stem.get(keyframe_id) or []
        if len(matches) != 1:
            _fail(
                "visual_semantic_input_keyframe_path_invalid",
                detail=keyframe_id,
            )
        if sha256_file(matches[0]) != expected_hash:
            _fail(
                "visual_semantic_input_keyframe_hash_mismatch",
                detail=keyframe_id,
            )
    _validate_source_schema(
        "input",
        input_manifest,
        code="visual_semantic_input_source_schema_invalid",
    )
    return structure_segments, len(frozen_hashes)


def publish_visual_semantic_contract(
    *,
    tool_root: Path,
    asset_id: str,
    source_version: str,
    analysis_run_id: str,
    current_visual_structure_run_id: str,
    current_visual_structure_result_hash: str,
    candidate: Mapping[str, Any],
    visual_prompt_version: str,
    model_config_id: str,
    write: bool = True,
) -> tuple[dict[str, Any], str, str]:
    """Validate a frozen current upstream snapshot and publish three artifacts."""

    structure_segments, keyframe_count = _validate_input_snapshot(
        tool_root=tool_root,
        asset_id=asset_id,
        source_version=source_version,
        current_visual_structure_run_id=current_visual_structure_run_id,
        current_visual_structure_result_hash=current_visual_structure_result_hash,
        visual_prompt_version=visual_prompt_version,
        model_config_id=model_config_id,
    )
    normalized = validate_visual_semantic_candidate(
        structure_segments=structure_segments,
        candidate=candidate,
        sampling_strategy=SAMPLING_STRATEGY,
    )
    payload = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "asset_id": asset_id,
        "source_version": source_version,
        "analysis_run_id": analysis_run_id,
        "visual_structure_run_id": current_visual_structure_run_id,
        "visual_structure_result_hash": current_visual_structure_result_hash,
        "sampling_strategy": SAMPLING_STRATEGY,
        "visual_prompt_version": visual_prompt_version,
        "model_config_id": model_config_id,
        "items": normalized["items"],
    }
    _validate_source_schema(
        "segments",
        payload,
        code="visual_semantic_segments_source_schema_invalid",
    )
    digest = result_hash(payload)
    if write:
        _write_json(tool_root / RESULT_PATH, payload)
        _write_json(
            tool_root / MANIFEST_PATH,
            {
                "schema_version": "media_library_visual_semantic_manifest_v2",
                "asset_id": asset_id,
                "source_version": source_version,
                "analysis_run_id": analysis_run_id,
                "visual_structure_run_id": current_visual_structure_run_id,
                "visual_structure_result_hash": current_visual_structure_result_hash,
                "sampling_strategy": SAMPLING_STRATEGY,
                "visual_prompt_version": visual_prompt_version,
                "model_config_id": model_config_id,
                "result_hash": digest,
                "result_path": RESULT_PATH,
                "fragment_count": len(payload["items"]),
                "keyframe_count": keyframe_count,
            },
        )
        _write_json(
            tool_root / QUALITY_PATH,
            {
                "schema_version": "media_library_visual_semantic_quality_v2",
                "valid": True,
                "fragment_count": len(payload["items"]),
                "keyframe_count": keyframe_count,
                "result_hash": digest,
                "sampling_strategy": SAMPLING_STRATEGY,
                "action_claim_count": 0,
            },
        )
    return payload, digest, RESULT_PATH


__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "INPUT_KEYFRAMES_REL",
    "INPUT_MANIFEST_REL",
    "INPUT_ROOT_REL",
    "INPUT_SCHEMA_VERSION",
    "INPUT_STRUCTURE_MANIFEST_REL",
    "INPUT_STRUCTURE_SEGMENTS_REL",
    "MANIFEST_PATH",
    "QUALITY_PATH",
    "RESULT_PATH",
    "RESULT_SCHEMA_VERSION",
    "SAMPLING_STRATEGY",
    "VisualSemanticValidationError",
    "publish_visual_semantic_contract",
    "validate_published_visual_semantic_result",
    "validate_visual_semantic_candidate",
    "validate_visual_semantic_item",
    "validate_with_single_repair",
]
