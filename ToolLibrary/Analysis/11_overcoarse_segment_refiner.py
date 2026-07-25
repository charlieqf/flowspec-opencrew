from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "OverCoarseSegmentRefiner"
TOOL_VERSION = "0.1.0"

HIGH_VALUE_BOUNDARY_TYPES = {
    "prompt_anchor",
    "problem_to_solution",
    "question_to_answer",
    "setup_to_turning_point",
    "topic_shift",
    "speaker_change",
    "emotion_shift",
}


class DependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Paths:
    workspace: Path | None
    meta_dir: Path


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_paths(workspace: Path | None, output_dir: Path | None) -> Paths:
    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    if output_dir is not None:
        meta_dir = output_dir.expanduser().resolve()
    elif resolved_workspace is not None:
        meta_dir = resolved_workspace / "meta"
    else:
        meta_dir = Path.cwd() / "meta"
    return Paths(workspace=resolved_workspace, meta_dir=meta_dir)


def optional_items(meta_dir: Path, filename: str, key: str = "items") -> list[dict[str, Any]]:
    path = meta_dir / filename
    if not path.exists():
        return []
    payload = read_json(path)
    value = payload.get(key) if isinstance(payload, dict) else None
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def load_required_semantic_segments(meta_dir: Path) -> list[dict[str, Any]]:
    path = meta_dir / "semantic_segment_candidates.json"
    if not path.exists():
        raise DependencyError(
            "OverCoarseSegmentRefiner requires 03 SemanticLLMStructureBuilder output: "
            f"{path}. Run 03_semantic_llm_structure_builder.py before 11, or point --output-dir to a meta directory containing semantic_segment_candidates.json."
        )
    payload = read_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise DependencyError(f"Invalid 03 dependency output: {path} must contain an items list.")
    segments = [item for item in items if isinstance(item, dict)]
    if not segments:
        raise DependencyError(f"Invalid 03 dependency output: {path} has no semantic segment items.")
    return segments


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def segment_duration(segment: dict[str, Any]) -> float:
    return max(0.0, safe_float(segment.get("end")) - safe_float(segment.get("start")))


def chars_per_second(segment: dict[str, Any]) -> float:
    text = str(segment.get("dialogue_text") or "")
    duration = segment_duration(segment)
    return len(text) / duration if duration > 0 else 0.0


def load_alignment_candidates(meta_dir: Path) -> tuple[bool, list[dict[str, Any]]]:
    path = meta_dir / "boundary_alignment.json"
    if not path.exists():
        return False, []
    payload = read_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        boundary_type = str(item.get("semantic_boundary_type") or "")
        confidence = safe_float(item.get("confidence"), safe_float(item.get("semantic_confidence")))
        priority = 70 if boundary_type in HIGH_VALUE_BOUNDARY_TYPES else 55
        if item.get("snap_strength") == "strong_snap":
            priority += 8
        elif item.get("snap_strength") == "weak_snap":
            priority += 3
        rows.append({
            "time": round(safe_float(item.get("aligned_time"), safe_float(item.get("original_time"))), 3),
            "source": "boundary_alignment",
            "source_ref": item.get("semantic_boundary_id"),
            "type": boundary_type,
            "confidence": confidence,
            "priority": priority,
            "reason": item.get("reason") or item.get("alignment_type") or "boundary_alignment",
        })
    return True, rows


def load_promoted_candidates(meta_dir: Path) -> tuple[bool, list[dict[str, Any]]]:
    path = meta_dir / "promoted_visual_boundaries.json"
    if not path.exists():
        return False, []
    payload = read_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "time": round(safe_float(item.get("time")), 3),
            "source": "promoted_visual_boundary",
            "source_ref": item.get("id") or item.get("source_ref"),
            "type": item.get("type") or item.get("source"),
            "confidence": safe_float(item.get("confidence"), 0.0),
            "priority": 100,
            "reason": item.get("promotion_reason") or "promoted_visual_boundary",
        })
    return True, rows


def is_inside_segment(candidate: dict[str, Any], segment: dict[str, Any], edge_guard: float) -> bool:
    time_value = safe_float(candidate.get("time"))
    return safe_float(segment.get("start")) + edge_guard <= time_value <= safe_float(segment.get("end")) - edge_guard


def split_durations(segment: dict[str, Any], boundaries: list[float]) -> list[float]:
    points = [safe_float(segment.get("start"))] + sorted(boundaries) + [safe_float(segment.get("end"))]
    return [round(points[index + 1] - points[index], 3) for index in range(len(points) - 1)]


def can_add_boundary(segment: dict[str, Any], selected: list[float], candidate_time: float, min_duration: float) -> bool:
    durations = split_durations(segment, selected + [candidate_time])
    return bool(durations) and min(durations) >= min_duration


def choose_split_candidates(segment: dict[str, Any], candidates: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    scoped = [item for item in candidates if is_inside_segment(item, segment, float(args.min_resulting_segment_duration))]
    scoped = [item for item in scoped if safe_float(item.get("confidence"), 0.0) >= float(args.min_candidate_confidence)]
    scoped.sort(key=lambda item: (int(item.get("priority") or 0), safe_float(item.get("confidence")), -abs(safe_float(item.get("time")) - (safe_float(segment.get("start")) + segment_duration(segment) / 2.0))), reverse=True)
    selected: list[dict[str, Any]] = []
    selected_times: list[float] = []
    for candidate in scoped:
        time_value = safe_float(candidate.get("time"))
        if any(abs(time_value - existing) <= float(args.boundary_dedupe_window) for existing in selected_times):
            continue
        if not can_add_boundary(segment, selected_times, time_value, float(args.min_resulting_segment_duration)):
            continue
        selected.append(candidate)
        selected_times.append(time_value)
        if len(selected) >= int(args.max_splits_per_segment):
            break
    return sorted(selected, key=lambda item: safe_float(item.get("time")))


def should_refine(segment: dict[str, Any], candidates: list[dict[str, Any]], args: argparse.Namespace) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    duration = segment_duration(segment)
    density = chars_per_second(segment)
    if duration >= float(args.min_overcoarse_duration):
        reasons.append("segment_duration_exceeds_threshold")
    if density >= float(args.min_dialogue_chars_per_second):
        reasons.append("dialogue_density_exceeds_threshold")
    if any(item.get("source") == "promoted_visual_boundary" for item in candidates):
        reasons.append("contains_promoted_visual_boundary")
    if duration >= float(args.min_internal_candidate_segment_duration) and len(candidates) >= int(args.min_internal_candidate_count):
        reasons.append("contains_multiple_internal_boundary_candidates")
    return bool(reasons), reasons


def build_subsegments(segment: dict[str, Any], boundaries: list[float]) -> list[dict[str, Any]]:
    points = [safe_float(segment.get("start"))] + sorted(boundaries) + [safe_float(segment.get("end"))]
    rows = []
    for index, (start, end) in enumerate(zip(points, points[1:]), start=1):
        rows.append({"index": index, "start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)})
    return rows


def dependency_status(meta_dir: Path, used_alignment: bool, used_promoted: bool) -> dict[str, str]:
    return {
        "semantic_segment_candidates": "required_present",
        "boundary_alignment": "used" if used_alignment else "missing",
        "promoted_visual_boundaries": "used" if used_promoted else "missing",
    }


def render_summary(payload: dict[str, Any]) -> str:
    counts = payload.get("counts") or {}
    lines = [
        "# Over-Coarse Refinement Summary",
        "",
        f"- Tool: {TOOL_NAME} {TOOL_VERSION}",
        f"- Semantic Segments: {counts.get('semantic_segments', 0)}",
        f"- Segments Suggested For Split: {counts.get('segments_with_split_suggestions', 0)}",
        f"- Suggested New Boundaries: {counts.get('suggested_boundaries', 0)}",
        "",
    ]
    for item in payload.get("items") or []:
        if item.get("action") != "split_suggested":
            continue
        lines.append(f"- Segment {item['segment_index']}: {item['segment_start']}s-{item['segment_end']}s -> boundaries {item['new_boundaries']}, reasons={','.join(item.get('trigger_reasons') or [])}")
    lines.append("")
    return "\n".join(lines)


def run_refiner(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    segments = load_required_semantic_segments(paths.meta_dir)
    used_alignment, alignment_candidates = load_alignment_candidates(paths.meta_dir)
    used_promoted, promoted_candidates = load_promoted_candidates(paths.meta_dir)
    all_candidates = alignment_candidates + promoted_candidates

    items: list[dict[str, Any]] = []
    for segment in segments:
        scoped = [candidate for candidate in all_candidates if is_inside_segment(candidate, segment, float(args.min_resulting_segment_duration))]
        should_split, trigger_reasons = should_refine(segment, scoped, args)
        selected = choose_split_candidates(segment, scoped, args) if should_split else []
        boundaries = [round(safe_float(item.get("time")), 3) for item in selected]
        action = "split_suggested" if boundaries else "no_action"
        items.append({
            "segment_index": int(segment.get("index") or 0),
            "segment_title": segment.get("title") or "",
            "segment_start": round(safe_float(segment.get("start")), 3),
            "segment_end": round(safe_float(segment.get("end")), 3),
            "segment_duration": round(segment_duration(segment), 3),
            "dialogue_chars": len(str(segment.get("dialogue_text") or "")),
            "dialogue_chars_per_second": round(chars_per_second(segment), 3),
            "action": action,
            "trigger_reasons": trigger_reasons,
            "new_boundaries": boundaries,
            "candidate_count": len(scoped),
            "selected_candidates": selected,
            "suggested_subsegments": build_subsegments(segment, boundaries) if boundaries else [],
        })

    split_items = [item for item in items if item.get("action") == "split_suggested"]
    payload = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workspace": str(paths.workspace) if paths.workspace else "",
        "mode": "suggestions_only",
        "dependency_status": dependency_status(paths.meta_dir, used_alignment, used_promoted),
        "parameters": {
            "min_overcoarse_duration": float(args.min_overcoarse_duration),
            "min_dialogue_chars_per_second": float(args.min_dialogue_chars_per_second),
            "min_resulting_segment_duration": float(args.min_resulting_segment_duration),
            "min_candidate_confidence": float(args.min_candidate_confidence),
            "min_internal_candidate_count": int(args.min_internal_candidate_count),
            "min_internal_candidate_segment_duration": float(args.min_internal_candidate_segment_duration),
            "max_splits_per_segment": int(args.max_splits_per_segment),
        },
        "counts": {
            "semantic_segments": len(segments),
            "alignment_candidates": len(alignment_candidates),
            "promoted_candidates": len(promoted_candidates),
            "segments_with_split_suggestions": len(split_items),
            "suggested_boundaries": sum(len(item.get("new_boundaries") or []) for item in split_items),
        },
        "items": items,
    }
    write_json(paths.meta_dir / "overcoarse_refinement.json", payload)
    write_text(paths.meta_dir / "overcoarse_refinement_summary.md", render_summary(payload))
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace": str(paths.workspace) if paths.workspace else "",
        "mode": "suggestions_only",
        "outputs": {
            "overcoarse_refinement": str(paths.meta_dir / "overcoarse_refinement.json"),
            "summary": str(paths.meta_dir / "overcoarse_refinement_summary.md"),
        },
        "counts": payload["counts"],
        "dependency_status": payload["dependency_status"],
    }
    write_json(paths.meta_dir / "11_overcoarse_segment_refiner_result.json", result)
    return result


def failed_result(paths: Paths, message: str) -> dict[str, Any]:
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "failed",
        "workspace": str(paths.workspace) if paths.workspace else "",
        "error_code": "missing_dependency",
        "message": message,
        "required_dependencies": ["03 semantic_segment_candidates.json"],
        "optional_dependencies": ["08 boundary_alignment.json", "09 promoted_visual_boundaries.json"],
    }
    write_json(paths.meta_dir / "11_overcoarse_segment_refiner_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Suggest splits for over-coarse semantic segments without modifying the timeline.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--min-overcoarse-duration", type=float, default=18.0)
    parser.add_argument("--min-dialogue-chars-per-second", type=float, default=8.5)
    parser.add_argument("--min-resulting-segment-duration", type=float, default=8.0)
    parser.add_argument("--min-candidate-confidence", type=float, default=0.78)
    parser.add_argument("--min-internal-candidate-count", type=int, default=2)
    parser.add_argument("--min-internal-candidate-segment-duration", type=float, default=15.0)
    parser.add_argument("--max-splits-per-segment", type=int, default=3)
    parser.add_argument("--boundary-dedupe-window", type=float, default=0.5)
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None)
    try:
        result = run_refiner(paths, args)
        exit_code = 0
    except DependencyError as exc:
        result = failed_result(paths, str(exc))
        exit_code = 2
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("status") == "completed":
        print(f"{TOOL_NAME} completed: {result['outputs']['overcoarse_refinement']}")
    else:
        print(f"{TOOL_NAME} failed: {result.get('message')}")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
