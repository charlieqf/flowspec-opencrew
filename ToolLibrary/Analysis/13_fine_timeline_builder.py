from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "FineTimelineBuilder"
TOOL_VERSION = "0.2.0"


class DependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Paths:
    workspace: Path | None
    meta_dir: Path
    storyboards_dir: Path


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_paths(workspace: Path | None, output_dir: Path | None, storyboards_dir: Path | None) -> Paths:
    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    if output_dir is not None:
        meta_dir = output_dir.expanduser().resolve()
    elif resolved_workspace is not None:
        meta_dir = resolved_workspace / "meta"
    else:
        meta_dir = Path.cwd() / "meta"
    if storyboards_dir is not None:
        resolved_storyboards = storyboards_dir.expanduser().resolve()
    elif resolved_workspace is not None:
        resolved_storyboards = resolved_workspace / "storyboards"
    else:
        resolved_storyboards = Path.cwd() / "storyboards"
    return Paths(workspace=resolved_workspace, meta_dir=meta_dir, storyboards_dir=resolved_storyboards)


def optional_items(meta_dir: Path, filename: str, key: str = "items") -> list[dict[str, Any]]:
    path = meta_dir / filename
    if not path.exists():
        return []
    payload = read_json(path)
    value = payload.get(key) if isinstance(payload, dict) else None
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_required_segments(meta_dir: Path) -> list[dict[str, Any]]:
    path = meta_dir / "semantic_segment_candidates.json"
    if not path.exists():
        raise DependencyError(f"FineTimelineBuilder requires 03 semantic_segment_candidates.json: {path}")
    payload = read_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise DependencyError(f"Invalid 03 dependency output: {path} must contain non-empty items list")
    return sorted([item for item in items if isinstance(item, dict)], key=lambda item: safe_float(item.get("start")))


def load_scene_srt_segments(meta_dir: Path) -> list[dict[str, Any]]:
    path = meta_dir / "scene_srt_segments.json"
    if not path.exists():
        return []
    payload = read_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    return sorted([item for item in items if isinstance(item, dict)], key=lambda item: safe_float(item.get("start"))) if isinstance(items, list) else []


def load_asr_sentence_timeline(meta_dir: Path) -> list[dict[str, Any]]:
    return sorted(optional_items(meta_dir, "asr_sentence_timeline.json"), key=lambda item: safe_float(item.get("start")))


def load_subtitle_alignment(meta_dir: Path) -> list[dict[str, Any]]:
    return sorted(optional_items(meta_dir, "subtitle_alignment_timeline.json"), key=lambda item: safe_float(item.get("start")))


def load_video_duration(meta_dir: Path, segments: list[dict[str, Any]]) -> float:
    path = meta_dir / "video_metadata.json"
    if path.exists():
        duration = safe_float(read_json(path).get("duration_seconds"), 0.0)
        if duration > 0:
            return duration
    fallback = max([safe_float(item.get("end")) for item in segments] + [0.0])
    if fallback <= 0:
        raise DependencyError("FineTimelineBuilder requires video duration from video_metadata.json or segment end times")
    return fallback


def segment_for_time(segments: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    midpoint = (start + end) / 2.0
    matched = [s for s in segments if safe_float(s.get("start")) <= midpoint <= safe_float(s.get("end"))]
    if matched:
        return matched
    overlaps = []
    for s in segments:
        if safe_float(s.get("start")) < end and safe_float(s.get("end")) > start:
            overlaps.append(s)
    return overlaps[:1]


def ranges_overlap(left_start: float, left_end: float, right_start: float, right_end: float) -> bool:
    left_end = max(left_start, left_end)
    right_end = max(right_start, right_end)
    return max(left_start, right_start) <= min(left_end, right_end)


def overlapping_items(start: float, end: float, items: list[dict[str, Any]], window: float = 0.0) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        item_start = safe_float(item.get("start"), safe_float(item.get("time")))
        item_end = safe_float(item.get("end"), item_start)
        if ranges_overlap(start - window, end + window, item_start, item_end):
            rows.append(item)
    return rows


def sentence_rows_for_semantic_segment(segment: dict[str, Any], asr_sentences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"), start)
    rows = []
    for sentence in asr_sentences:
        sentence_start = safe_float(sentence.get("start"))
        sentence_end = safe_float(sentence.get("end"), sentence_start)
        midpoint = (sentence_start + sentence_end) / 2.0
        if start <= midpoint <= end or ranges_overlap(start, end, sentence_start, sentence_end):
            rows.append(sentence)
    return rows


def build_detail_from_semantic_segments(meta_dir: Path, semantic_segments: list[dict[str, Any]], asr_sentences: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    subtitle_alignment = load_subtitle_alignment(meta_dir)
    visual_text = optional_items(meta_dir, "visual_text_timeline.json")
    rows = []
    used_sentence_ids: set[str] = set()
    for segment in semantic_segments:
        sentence_rows = [row for row in sentence_rows_for_semantic_segment(segment, asr_sentences) if str(row.get("id") or row.get("index") or "") not in used_sentence_ids]
        if not sentence_rows:
            start = safe_float(segment.get("start"))
            end = safe_float(segment.get("end"), start)
            if end <= start:
                continue
            dialogue_text = str(segment.get("dialogue_text") or "").strip()
            sentence_refs: list[str] = []
        else:
            for sentence in sentence_rows:
                used_sentence_ids.add(str(sentence.get("id") or sentence.get("index") or ""))
            start = min(safe_float(sentence.get("start")) for sentence in sentence_rows)
            end = max(safe_float(sentence.get("end"), safe_float(sentence.get("start"))) for sentence in sentence_rows)
            dialogue_text = "".join(str(sentence.get("text") or "") for sentence in sentence_rows).strip()
            sentence_refs = [str(sentence.get("id") or sentence.get("index") or "") for sentence in sentence_rows]
        aligned = overlapping_items(start, end, subtitle_alignment, window=0.2)
        visual_context = [str(item.get("text") or item.get("ocr_text") or "") for item in overlapping_items(start, end, visual_text, window=0.2)]
        index = len(rows) + 1
        rows.append({
            "scheme": "detail",
            "index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "title": segment.get("title") or f"Semantic Segment {segment.get('index') or index}",
            "semantic_role": segment.get("semantic_role") or "semantic_detail",
            "formula_slot": segment.get("formula_slot") or "",
            "summary": segment.get("summary") or "",
            "dialogue_text": dialogue_text,
            "confidence": safe_float(segment.get("confidence"), 0.86),
            "source_segment_indices": [int(segment.get("index") or index)],
            "source_sentence_ids": sentence_refs,
            "source_unit_ids": segment.get("source_unit_ids") or [],
            "boundary_source": "03_semantic_segment+asr_sentence_timeline",
            "boundary_ref": f"semantic_segment_{segment.get('index') or index}",
            "boundary_type": "llm_semantic_merge_with_provider_word_timestamps",
            "boundary_reason": segment.get("boundary_reason") or "",
            "evidence_refs": [item.get("id") for item in aligned if item.get("id")],
            "visual_text_context": [value for value in visual_context if value],
            "subtitle_alignment_refs": [item.get("id") for item in aligned if item.get("id")],
        })
    if rows:
        rows[0]["start"] = 0.0
        rows[0]["duration"] = round(safe_float(rows[0].get("end")) - safe_float(rows[0].get("start")), 3)
        rows[-1]["end"] = round(duration, 3)
        rows[-1]["duration"] = round(safe_float(rows[-1].get("end")) - safe_float(rows[-1].get("start")), 3)
        resolve_adjacent_overlaps(rows)
    return rows


def resolve_adjacent_overlaps(rows: list[dict[str, Any]]) -> None:
    for left, right in zip(rows, rows[1:]):
        left_start = safe_float(left.get("start"))
        left_end = safe_float(left.get("end"), left_start)
        right_start = safe_float(right.get("start"))
        if left_end <= right_start:
            continue
        if right_start > left_start:
            left["end"] = round(right_start, 3)
            left["duration"] = round(right_start - left_start, 3)
            left.setdefault("timeline_adjustments", []).append({
                "type": "trim_end_to_next_start",
                "original_end": round(left_end, 3),
                "reason": "overlapping_asr_sentence_timeline",
            })
            continue
        right_end = safe_float(right.get("end"), right_start)
        if left_end < right_end:
            right["start"] = round(left_end, 3)
            right["duration"] = round(right_end - left_end, 3)
            right.setdefault("timeline_adjustments", []).append({
                "type": "shift_start_to_previous_end",
                "original_start": round(right_start, 3),
                "reason": "overlapping_asr_sentence_timeline",
            })


def collect_08_boundaries(meta_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for item in optional_items(meta_dir, "boundary_alignment.json"):
        rows.append({"time": safe_float(item.get("aligned_time")), "source": "08_boundary_alignment", "ref": item.get("semantic_boundary_id"), "type": item.get("semantic_boundary_type"), "confidence": safe_float(item.get("confidence"), 0.0)})
    return rows


def collect_09_boundaries(meta_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for item in optional_items(meta_dir, "promoted_visual_boundaries.json"):
        rows.append({"time": safe_float(item.get("time")), "source": "09_promoted_visual", "ref": item.get("id") or item.get("source_ref"), "type": item.get("type"), "confidence": safe_float(item.get("confidence"), 0.0)})
    return rows


def collect_03_semantic_boundaries(meta_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for item in optional_items(meta_dir, "semantic_boundary_candidates.json"):
        rows.append({"time": safe_float(item.get("time")), "source": "03_semantic_boundary", "ref": item.get("id"), "type": item.get("type"), "confidence": safe_float(item.get("confidence"), 0.0)})
    return rows


def collect_11_boundaries(meta_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for item in optional_items(meta_dir, "overcoarse_refinement.json"):
        if item.get("action") != "split_suggested":
            continue
        for boundary in item.get("new_boundaries") or []:
            rows.append({"time": safe_float(boundary), "source": "11_overcoarse", "ref": f"segment_{item.get('segment_index')}", "type": "overcoarse_split", "confidence": 0.8})
    return rows


def collect_12_merges(meta_dir: Path) -> set[int]:
    merged = set()
    for item in optional_items(meta_dir, "overfragmented_merge_decision.json"):
        if item.get("action") == "merge_suggested":
            merged.add(int(item.get("segment_index") or 0))
    return merged


def add_boundary(boundaries: list[dict[str, Any]], row: dict[str, Any], min_gap: float, duration: float) -> None:
    time_value = round(safe_float(row.get("time")), 3)
    if time_value <= 0 or time_value >= duration:
        return
    if any(abs(time_value - safe_float(existing.get("time"))) < min_gap for existing in boundaries):
        return
    row = dict(row)
    row["time"] = time_value
    boundaries.append(row)


def build_points(boundaries: list[dict[str, Any]], duration: float) -> list[float]:
    points = [0.0] + sorted({round(safe_float(item.get("time")), 3) for item in boundaries if 0 < safe_float(item.get("time")) < duration}) + [round(duration, 3)]
    return points


def source_summary(source_segments: list[dict[str, Any]]) -> dict[str, Any]:
    if not source_segments:
        return {"title": "未匹配语义段", "semantic_role": "gap_or_context", "formula_slot": "", "dialogue_text": "", "confidence": 0.5, "source_segment_indices": []}
    titles = [str(s.get("title") or "") for s in source_segments if s.get("title")]
    roles = [str(s.get("semantic_role") or "") for s in source_segments if s.get("semantic_role")]
    slots = [str(s.get("formula_slot") or "") for s in source_segments]
    texts = [str(s.get("dialogue_text") or "") for s in source_segments if s.get("dialogue_text")]
    confidences = [safe_float(s.get("confidence"), 0.8) for s in source_segments]
    return {
        "title": " / ".join(titles[:2]) if titles else "语义片段",
        "semantic_role": roles[0] if roles else "",
        "formula_slot": next((slot for slot in slots if slot), ""),
        "dialogue_text": "".join(texts),
        "confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0.8,
        "source_segment_indices": [int(s.get("index") or 0) for s in source_segments],
    }


def build_segments_from_boundaries(scheme: str, boundaries: list[dict[str, Any]], source_segments: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    boundary_by_time = {round(safe_float(item.get("time")), 3): item for item in boundaries}
    points = build_points(boundaries, duration)
    rows = []
    for index, (start, end) in enumerate(zip(points, points[1:]), start=1):
        matched = segment_for_time(source_segments, start, end)
        summary = source_summary(matched)
        boundary_ref = boundary_by_time.get(round(end, 3), {}) if end < duration else {}
        rows.append({
            "scheme": scheme,
            "index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            **summary,
            "boundary_source": boundary_ref.get("source") or ("video_end" if end >= duration else "continuous_coverage"),
            "boundary_ref": boundary_ref.get("ref"),
            "boundary_type": boundary_ref.get("type"),
            "evidence_refs": [],
        })
    return rows


def semantic_segment_boundaries(segments: list[dict[str, Any]], duration: float, merged_indices: set[int] | None = None) -> list[dict[str, Any]]:
    merged_indices = merged_indices or set()
    rows = []
    for left, right in zip(segments, segments[1:]):
        if int(right.get("index") or 0) in merged_indices:
            continue
        rows.append({"time": safe_float(right.get("start")), "source": "03_semantic_segment", "ref": f"segment_{right.get('index')}_start", "type": "semantic_segment_boundary", "confidence": safe_float(right.get("confidence"), 0.8)})
    return [row for row in rows if 0 < safe_float(row.get("time")) < duration]


def build_detail_scheme(meta_dir: Path, segments: list[dict[str, Any]], duration: float, args: argparse.Namespace) -> list[dict[str, Any]]:
    asr_sentences = load_asr_sentence_timeline(meta_dir)
    if asr_sentences:
        return build_detail_from_semantic_segments(meta_dir, segments, asr_sentences, duration)
    scene_srt_segments = load_scene_srt_segments(meta_dir)
    if scene_srt_segments:
        rows = []
        for index, segment in enumerate(scene_srt_segments, start=1):
            start = safe_float(segment.get("start"))
            end = safe_float(segment.get("end"), start)
            rows.append({
                "scheme": "detail",
                "index": index,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
                "title": segment.get("title") or f"Scene {segment.get('source_scene_index') or index}",
                "semantic_role": segment.get("semantic_role") or "scene_srt_calibrated",
                "formula_slot": segment.get("formula_slot") or "",
                "dialogue_text": segment.get("dialogue_text") or "",
                "confidence": safe_float(segment.get("confidence"), 0.8),
                "source_segment_indices": [int(segment.get("source_scene_index") or index)],
                "boundary_source": "13_01_scene_srt_calibrated",
                "boundary_ref": segment.get("srt_path"),
                "boundary_type": segment.get("alignment_policy") or "scene_srt_calibrated",
                "evidence_refs": [segment.get("srt_path")] if segment.get("srt_path") else [],
                "visual_text_context": segment.get("visual_text_context") or [],
            })
        return rows
    boundaries: list[dict[str, Any]] = []
    if str(args.detail_boundary_source) == "semantic":
        source_boundaries = collect_03_semantic_boundaries(meta_dir)
    else:
        source_boundaries = collect_08_boundaries(meta_dir) + collect_09_boundaries(meta_dir)
    for row in source_boundaries:
        add_boundary(boundaries, row, float(args.detail_boundary_dedupe_window), duration)
    return build_segments_from_boundaries("detail", boundaries, segments, duration)


def build_balanced_scheme(meta_dir: Path, segments: list[dict[str, Any]], duration: float, args: argparse.Namespace) -> list[dict[str, Any]]:
    boundaries: list[dict[str, Any]] = []
    merged_indices = collect_12_merges(meta_dir)
    for row in semantic_segment_boundaries(segments, duration, merged_indices):
        add_boundary(boundaries, row, float(args.balanced_boundary_dedupe_window), duration)
    for row in collect_09_boundaries(meta_dir) + collect_11_boundaries(meta_dir):
        candidate = boundaries + [row]
        points = build_points(candidate, duration)
        if min([b - a for a, b in zip(points, points[1:])] or [duration]) >= float(args.balanced_min_segment_duration):
            add_boundary(boundaries, row, float(args.balanced_boundary_dedupe_window), duration)
    return build_segments_from_boundaries("balanced", boundaries, segments, duration)


def build_summary_scheme(segments: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for segment in segments:
        slot = str(segment.get("formula_slot") or "未匹配/导流")
        if groups and str(groups[-1][-1].get("formula_slot") or "未匹配/导流") == slot:
            groups[-1].append(segment)
        else:
            groups.append([segment])
    rows = []
    for index, group in enumerate(groups, start=1):
        start = 0.0 if index == 1 else safe_float(group[0].get("start"))
        end = duration if index == len(groups) else safe_float(group[-1].get("end"))
        summary = source_summary(group)
        slot = str(group[0].get("formula_slot") or "未匹配/导流")
        rows.append({
            "scheme": "summary",
            "index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            **summary,
            "formula_slot": slot,
            "boundary_source": "formula_slot_grouping",
            "evidence_refs": [],
        })
    for left, right in zip(rows, rows[1:]):
        right["start"] = left["end"]
        right["duration"] = round(right["end"] - right["start"], 3)
    if rows:
        rows[0]["start"] = 0.0
        rows[0]["duration"] = round(rows[0]["end"] - rows[0]["start"], 3)
        rows[-1]["end"] = round(duration, 3)
        rows[-1]["duration"] = round(rows[-1]["end"] - rows[-1]["start"], 3)
    return rows


def coverage_check(items: list[dict[str, Any]], duration: float) -> dict[str, Any]:
    eps = 0.001
    gaps = []
    overlaps = []
    positive = True
    for item in items:
        if safe_float(item.get("end")) - safe_float(item.get("start")) <= 0:
            positive = False
    for left, right in zip(items, items[1:]):
        delta = safe_float(right.get("start")) - safe_float(left.get("end"))
        if delta > eps:
            gaps.append({"after": left.get("index"), "before": right.get("index"), "gap": round(delta, 3)})
        elif delta < -eps:
            overlaps.append({"left": left.get("index"), "right": right.get("index"), "overlap": round(-delta, 3)})
    starts_at_zero = bool(items) and abs(safe_float(items[0].get("start")) - 0.0) <= eps
    ends_at_duration = bool(items) and abs(safe_float(items[-1].get("end")) - duration) <= eps
    sentence_timed = bool(items) and all("asr_sentence_timeline" in str(item.get("boundary_source") or "") for item in items)
    # Word-level ASR sentence timelines intentionally preserve real speech gaps instead of stretching clips to cover silence.
    status = "passed" if ((starts_at_zero and ends_at_duration and not gaps and not overlaps) or (sentence_timed and not overlaps)) and positive else "failed"
    return {"status": status, "starts_at_zero": starts_at_zero, "ends_at_duration": ends_at_duration, "no_gaps": not gaps, "no_overlaps": not overlaps, "positive_duration": positive, "sentence_timed": sentence_timed, "gaps": gaps, "overlaps": overlaps, "min_duration": round(min([safe_float(i.get("duration")) for i in items] or [0.0]), 3), "segment_count": len(items)}


def render_storyboard(name: str, items: list[dict[str, Any]]) -> str:
    lines = [f"# {name.title()} Storyboard", ""]
    for item in items:
        lines.append(f"## {item['index']}. {item.get('title') or 'Segment'}")
        lines.append(f"- Time: {item['start']}s - {item['end']}s ({item['duration']}s)")
        lines.append(f"- Formula Slot: {item.get('formula_slot') or ''}")
        lines.append(f"- Semantic Role: {item.get('semantic_role') or ''}")
        lines.append(f"- Boundary Source: {item.get('boundary_source') or ''}")
        if item.get("dialogue_text"):
            lines.append(f"- Dialogue: {str(item.get('dialogue_text'))[:240]}")
        lines.append("")
    return "\n".join(lines)


def dependency_status(meta_dir: Path) -> dict[str, str]:
    return {
        "semantic_segment_candidates": "required_present",
        "video_metadata": "present" if (meta_dir / "video_metadata.json").exists() else "fallback",
        "boundary_alignment": "present" if (meta_dir / "boundary_alignment.json").exists() else "missing",
        "promoted_visual_boundaries": "present" if (meta_dir / "promoted_visual_boundaries.json").exists() else "missing",
        "evidence_index": "present" if (meta_dir / "evidence_index.json").exists() else "missing",
        "overcoarse_refinement": "present" if (meta_dir / "overcoarse_refinement.json").exists() else "missing",
        "overfragmented_merge_decision": "present" if (meta_dir / "overfragmented_merge_decision.json").exists() else "missing",
        "scene_srt_segments": "present" if (meta_dir / "scene_srt_segments.json").exists() else "missing",
    }


def run_builder(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    segments = load_required_segments(paths.meta_dir)
    duration = round(load_video_duration(paths.meta_dir, segments), 3)
    requested_schemes = [item.strip() for item in str(args.schemes).split(",") if item.strip()]
    if bool(args.detail_only):
        requested_schemes = ["detail"]
    invalid = sorted(set(requested_schemes) - {"detail", "balanced", "summary"})
    if invalid:
        raise DependencyError(f"Invalid schemes requested: {', '.join(invalid)}")
    schemes: dict[str, list[dict[str, Any]]] = {}
    if "detail" in requested_schemes:
        schemes["detail"] = build_detail_scheme(paths.meta_dir, segments, duration, args)
    if "balanced" in requested_schemes:
        schemes["balanced"] = build_balanced_scheme(paths.meta_dir, segments, duration, args)
    if "summary" in requested_schemes:
        schemes["summary"] = build_summary_scheme(segments, duration)
    checks = {name: coverage_check(items, duration) for name, items in schemes.items()}

    if "detail" in schemes:
        write_json(paths.meta_dir / "scheme_detail_segments.json", {"scheme": "detail", "items": schemes["detail"]})
        write_text(paths.storyboards_dir / "scheme_detail_storyboard.md", render_storyboard("detail", schemes["detail"]))
    if "balanced" in schemes:
        write_json(paths.meta_dir / "scheme_balanced_segments.json", {"scheme": "balanced", "items": schemes["balanced"]})
        write_text(paths.storyboards_dir / "scheme_balanced_storyboard.md", render_storyboard("balanced", schemes["balanced"]))
    if "summary" in schemes:
        write_json(paths.meta_dir / "scheme_summary_segments.json", {"scheme": "summary", "items": schemes["summary"], "formula_source": "semantic_segment_candidates.formula_slot"})
        write_text(paths.storyboards_dir / "scheme_summary_storyboard.md", render_storyboard("summary", schemes["summary"]))
    write_json(paths.meta_dir / "timeline_coverage_check.json", {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "video_duration": duration, "schemes": checks})

    default_scheme = str(args.default_scheme)
    if default_scheme not in schemes:
        default_scheme = requested_schemes[0] if requested_schemes else "detail"
    default_items = schemes[default_scheme]
    scheme_paths = {name: f"meta/scheme_{name}_segments.json" for name in schemes}
    fine_payload = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "default_scheme": default_scheme, "requested_schemes": requested_schemes, "video_duration": duration, "detail_boundary_source": str(args.detail_boundary_source), "dependency_status": dependency_status(paths.meta_dir), "coverage": checks[default_scheme], "schemes": scheme_paths, "items": default_items}
    write_json(paths.meta_dir / "fine_logical_segments.json", fine_payload)
    outputs = {"fine_logical_segments": str(paths.meta_dir / "fine_logical_segments.json"), "coverage": str(paths.meta_dir / "timeline_coverage_check.json")}
    outputs.update({name: str(paths.meta_dir / f"scheme_{name}_segments.json") for name in schemes})
    result = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "completed" if all(c["status"] == "passed" for c in checks.values()) else "failed", "workspace": str(paths.workspace) if paths.workspace else "", "default_scheme": default_scheme, "requested_schemes": requested_schemes, "detail_boundary_source": str(args.detail_boundary_source), "outputs": outputs, "counts": {name: len(items) for name, items in schemes.items()}, "coverage_status": {name: check["status"] for name, check in checks.items()}, "dependency_status": fine_payload["dependency_status"]}
    write_json(paths.meta_dir / "13_fine_timeline_builder_result.json", result)
    return result


def failed_result(paths: Paths, message: str) -> dict[str, Any]:
    result = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "failed", "workspace": str(paths.workspace) if paths.workspace else "", "error_code": "missing_dependency", "message": message, "required_dependencies": ["03 semantic_segment_candidates.json"], "optional_dependencies": ["08 boundary_alignment.json", "09 promoted_visual_boundaries.json", "10 evidence_index.json", "11 overcoarse_refinement.json", "12 overfragmented_merge_decision.json"]}
    write_json(paths.meta_dir / "13_fine_timeline_builder_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build complete fine timelines and three storyboard schemes.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta and <workspace>/storyboards.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--storyboards-dir", help="Explicit storyboards output directory. Overrides --workspace/storyboards.")
    parser.add_argument("--default-scheme", choices=["detail", "balanced", "summary"], default="detail")
    parser.add_argument("--schemes", default="detail", help="Comma-separated schemes to build. Default: detail.")
    parser.add_argument("--detail-only", action=argparse.BooleanOptionalAction, default=True, help="Build only detail scheme by default.")
    parser.add_argument("--detail-boundary-source", choices=["aligned_visual", "semantic"], default="aligned_visual", help="For detail scheme, use aligned visual boundaries from 08/09 or original ASR+OCR semantic boundaries from 03.")
    parser.add_argument("--detail-boundary-dedupe-window", type=float, default=0.15)
    parser.add_argument("--balanced-boundary-dedupe-window", type=float, default=0.5)
    parser.add_argument("--balanced-min-segment-duration", type=float, default=8.0)
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None, Path(args.storyboards_dir) if args.storyboards_dir else None)
    try:
        result = run_builder(paths, args)
        exit_code = 0 if result.get("status") == "completed" else 1
    except DependencyError as exc:
        result = failed_result(paths, str(exc))
        exit_code = 2
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("status") == "completed":
        print(f"{TOOL_NAME} completed: {result['outputs']['fine_logical_segments']}")
    else:
        print(f"{TOOL_NAME} failed: {result.get('message') or result.get('coverage_status')}")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
