from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from pathlib import Path
from typing import Any


NON_CONTENT_KEYS = {
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
    "elapsed_ms",
    "preview_url",
    "thumbnail_url",
    "download_url",
}


def new_analysis_run_id(scheme: str, timestamp_ms: int) -> str:
    return f"mlar_{scheme}_{timestamp_ms:013d}_{uuid.uuid4().hex[:12]}"


def seconds_to_ms(value: Any) -> int:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("time_not_finite")
    return round(number * 1000)


def _canonical_content(value: Any) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) in NON_CONTENT_KEYS or str(key).endswith("_url"):
                continue
            if isinstance(item, str) and os.path.isabs(item):
                continue
            normalized[str(key)] = _canonical_content(item)
        return normalized
    if isinstance(value, list):
        return [_canonical_content(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("result_contains_non_finite_number")
    return value


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        _canonical_content(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def result_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            data = handle.read(1024 * 1024)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path.name}")
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _normalize_source_interval(
    start_ms: int,
    end_ms: int,
    source_duration_ms: int | None,
) -> tuple[int, int] | None:
    """Normalize legacy tool boundaries before publishing strict ms ranges."""

    if start_ms < 0:
        raise ValueError("fragment_time_range_invalid")
    normalized_end = int(end_ms)
    if source_duration_ms is not None:
        normalized_end = min(normalized_end, int(source_duration_ms))
    if normalized_end <= start_ms:
        return None
    return int(start_ms), normalized_end


def publish_dialogue_contract(
    *,
    tool_root: Path,
    asset_id: str,
    source_version: str,
    analysis_run_id: str,
    source_duration_ms: int | None,
    write: bool = True,
) -> tuple[dict[str, Any], str, str]:
    final_payload = _read_object(
        tool_root / "SessionOutput" / "subtitle" / "final_srt_frame_items.json"
    )
    calibrated_path = (
        tool_root / "SessionOutput" / "subtitle" / "calibrated_srt_items.json"
    )
    calibrated_payload = (
        _read_object(calibrated_path) if calibrated_path.is_file() else {"items": []}
    )
    calibrated = {
        str(item.get("sentence_id") or ""): item
        for item in calibrated_payload.get("items") or []
        if isinstance(item, dict) and str(item.get("sentence_id") or "")
    }
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(final_payload.get("items") or [], start=1):
        if not isinstance(raw, dict):
            continue
        fragment_id = str(raw.get("srt_id") or f"srt_{index:04d}")
        start_ms = seconds_to_ms(raw.get("start") or 0)
        end_ms = seconds_to_ms(raw.get("end") or raw.get("start") or 0)
        normalized = _normalize_source_interval(
            start_ms, end_ms, source_duration_ms
        )
        if normalized is None:
            continue
        start_ms, end_ms = normalized
        if fragment_id in seen:
            raise ValueError("fragment_id_duplicate")
        seen.add(fragment_id)
        evidence = calibrated.get(fragment_id) or {}
        keyframe_refs = (
            [f"{fragment_id}-keyframe"]
            if str(raw.get("image_path") or evidence.get("frame_path") or "").strip()
            else []
        )
        items.append(
            {
                "fragment_id": fragment_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_ms": end_ms - start_ms,
                "dialogue_text": str(
                    raw.get("dialogue") or evidence.get("text") or ""
                ).strip(),
                "keyframe_refs": keyframe_refs,
                "confidence": raw.get("confidence"),
                "needs_review": bool(
                    (evidence.get("calibration") or {}).get("needs_review")
                    if isinstance(evidence.get("calibration"), dict)
                    else False
                ),
            }
        )
    payload = {
        "schema_version": "media_library_dialogue_fragments_v1",
        "asset_id": asset_id,
        "source_version": source_version,
        "analysis_run_id": analysis_run_id,
        "items": items,
    }
    digest = result_hash(payload)
    index_path = tool_root / "SessionOutput" / "json" / "dialogue_fragment_index.json"
    manifest_path = (
        tool_root / "SessionOutput" / "manifests" / "dialogue_analysis_manifest.json"
    )
    quality_path = tool_root / "SessionReport" / "dialogue_quality_check.json"
    if write:
        _write_json(index_path, payload)
        _write_json(
            manifest_path,
            {
                "schema_version": "media_library_dialogue_manifest_v1",
                "asset_id": asset_id,
                "source_version": source_version,
                "analysis_run_id": analysis_run_id,
                "result_hash": digest,
                "result_path": "SessionOutput/json/dialogue_fragment_index.json",
                "fragment_count": len(items),
            },
        )
        _write_json(
            quality_path,
            {
                "schema_version": "media_library_dialogue_quality_v1",
                "valid": True,
                "fragment_count": len(items),
                "result_hash": digest,
            },
        )
    return payload, digest, "SessionOutput/json/dialogue_fragment_index.json"


def publish_visual_structure_contract(
    *,
    tool_root: Path,
    asset_id: str,
    source_version: str,
    analysis_run_id: str,
    source_duration_ms: int | None,
    write: bool = True,
) -> tuple[dict[str, Any], str, str]:
    final_payload = _read_object(
        tool_root / "SessionOutput" / "visual" / "final_scene_frame_items.json"
    )
    if final_payload.get("sampling_strategy") != "scene_uniform_4_v1":
        raise ValueError("visual_structure_sampling_strategy_ineligible")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    root = tool_root.resolve()
    for index, raw in enumerate(final_payload.get("items") or [], start=1):
        if not isinstance(raw, dict):
            continue
        fragment_id = str(raw.get("scene_id") or f"scene_{index:04d}")
        start_ms = seconds_to_ms(raw.get("start") or 0)
        end_ms = seconds_to_ms(raw.get("end") or raw.get("start") or 0)
        normalized = _normalize_source_interval(
            start_ms, end_ms, source_duration_ms
        )
        if normalized is None:
            continue
        start_ms, end_ms = normalized
        if fragment_id in seen:
            raise ValueError("fragment_id_duplicate")
        seen.add(fragment_id)
        raw_keyframes = raw.get("keyframes")
        if not isinstance(raw_keyframes, list) or len(raw_keyframes) != 4:
            raise ValueError("visual_structure_four_keyframes_required")
        keyframes: list[dict[str, Any]] = []
        for slot_index, raw_keyframe in enumerate(raw_keyframes):
            if not isinstance(raw_keyframe, dict):
                raise ValueError("keyframe_invalid")
            expected_id = f"{fragment_id}-sample-{slot_index + 1:02d}"
            if raw_keyframe.get("keyframe_id") != expected_id:
                raise ValueError("keyframe_id_invalid")
            raw_image_path = str(raw_keyframe.get("image_path") or "")
            if not raw_image_path or Path(raw_image_path).is_absolute():
                raise ValueError("keyframe_path_invalid")
            image_rel = raw_image_path.replace("\\", "/")
            unresolved_image_path = root / image_rel
            image_path = unresolved_image_path.resolve()
            if (
                unresolved_image_path.is_symlink()
                or not image_path.is_relative_to(root)
                or not image_path.is_file()
            ):
                raise ValueError("keyframe_path_invalid")
            raw_time = raw_keyframe.get("keyframe_time")
            if raw_time is None:
                raise ValueError("keyframe_time_missing")
            keyframe_time_ms = seconds_to_ms(raw_time)
            if not start_ms <= keyframe_time_ms < end_ms:
                raise ValueError("keyframe_time_out_of_range")
            quarter_start = start_ms + ((end_ms - start_ms) * slot_index // 4)
            quarter_end = start_ms + ((end_ms - start_ms) * (slot_index + 1) // 4)
            slot_upper = end_ms if slot_index == 3 else quarter_end
            if not quarter_start <= keyframe_time_ms < slot_upper:
                raise ValueError("keyframe_time_outside_sampling_slot")
            image_sha256 = sha256_file(image_path)
            declared_hash = str(raw_keyframe.get("image_sha256") or "")
            if declared_hash and declared_hash != image_sha256:
                raise ValueError("keyframe_hash_mismatch")
            keyframes.append(
                {
                    "keyframe_id": expected_id,
                    "keyframe_time_ms": keyframe_time_ms,
                    "image_path": image_rel,
                    "image_sha256": image_sha256,
                }
            )
        items.append(
            {
                "fragment_id": fragment_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_ms": end_ms - start_ms,
                "keyframes": keyframes,
                "sampling_strategy": "scene_uniform_4_v1",
            }
        )
    if not items:
        raise ValueError("visual_structure_fragments_empty")
    payload = {
        "schema_version": "media_library_visual_structure_v2",
        "asset_id": asset_id,
        "source_version": source_version,
        "analysis_run_id": analysis_run_id,
        "sampling_strategy": "scene_uniform_4_v1",
        "items": items,
    }
    digest = result_hash(payload)
    segments_path = (
        tool_root / "SessionOutput" / "visual" / "visual_structure_segments.json"
    )
    manifest_path = (
        tool_root / "SessionOutput" / "visual" / "visual_structure_manifest.json"
    )
    quality_path = tool_root / "SessionReport" / "visual_structure_quality_check.json"
    if write:
        _write_json(segments_path, payload)
        _write_json(
            manifest_path,
            {
                "schema_version": "media_library_visual_structure_manifest_v2",
                "asset_id": asset_id,
                "source_version": source_version,
                "analysis_run_id": analysis_run_id,
                "result_hash": digest,
                "result_path": "SessionOutput/visual/visual_structure_segments.json",
                "sampling_strategy": "scene_uniform_4_v1",
                "fragment_count": len(items),
                "keyframe_count": len(items) * 4,
            },
        )
        _write_json(
            quality_path,
            {
                "schema_version": "media_library_visual_structure_quality_v2",
                "valid": True,
                "fragment_count": len(items),
                "result_hash": digest,
                "sampling_strategy": "scene_uniform_4_v1",
                "keyframe_count": len(items) * 4,
            },
        )
    return payload, digest, "SessionOutput/visual/visual_structure_segments.json"
