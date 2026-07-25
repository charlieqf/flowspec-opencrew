from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "VisualBoundaryPromoter"
TOOL_VERSION = "0.1.0"

STRONG_SEPARATOR_TYPES = {"black_screen", "white_screen", "solid_color_separator", "title_card", "chapter_card"}


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
            "VisualBoundaryPromoter requires 03 SemanticLLMStructureBuilder output: "
            f"{path}. Run 03_semantic_llm_structure_builder.py before 09, or point --output-dir to a meta directory containing semantic_segment_candidates.json."
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


def candidate_ref(prefix: str, item: dict[str, Any], fallback_time: float | None = None) -> str:
    raw_index = item.get("index") or item.get("source_cut_index") or item.get("cut_index")
    if raw_index is not None:
        return f"{prefix}_{raw_index}"
    time_value = safe_float(item.get("time"), fallback_time or 0.0)
    return f"{prefix}_t{time_value:.3f}"


def segment_boundary_times(segments: list[dict[str, Any]]) -> list[float]:
    values: set[float] = set()
    for segment in segments:
        values.add(round(safe_float(segment.get("start")), 3))
        values.add(round(safe_float(segment.get("end")), 3))
    return sorted(value for value in values if value >= 0.0)


def load_alignment_context(meta_dir: Path, ignore_boundary_alignment: bool) -> tuple[bool, set[str], list[float]]:
    path = meta_dir / "boundary_alignment.json"
    if ignore_boundary_alignment or not path.exists():
        return False, set(), []
    payload = read_json(path)
    used_refs = {str(item) for item in payload.get("used_visual_evidence_refs") or [] if item}
    aligned_times: list[float] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        aligned_times.append(round(safe_float(item.get("aligned_time")), 3))
        for ref in item.get("visual_evidence") or []:
            if ref:
                used_refs.add(str(ref))
    return True, used_refs, sorted(set(aligned_times))


def normalize_separator_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        time_value = safe_float(item.get("time"), -1.0)
        if time_value < 0:
            continue
        rows.append({
            "source_ref": candidate_ref("separator", item),
            "source": "separator",
            "time": round(time_value, 3),
            "type": str(item.get("type") or "separator_candidate"),
            "confidence": safe_float(item.get("confidence"), 0.0),
            "reason": item.get("reason") or item.get("type") or "separator_candidate",
            "evidence_frame": item.get("evidence_frame"),
            "raw": item,
        })
    return rows


def normalize_visual_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        time_value = safe_float(item.get("time"), -1.0)
        if time_value < 0:
            continue
        rows.append({
            "source_ref": candidate_ref("visual_boundary", item),
            "source": "visual_boundary",
            "time": round(time_value, 3),
            "type": str(item.get("type") or "visual_change"),
            "confidence": safe_float(item.get("confidence"), 0.0),
            "reason": item.get("reason") or item.get("type") or "visual_boundary",
            "evidence_frame": item.get("evidence_frame"),
            "raw": item,
        })
    return rows


def normalize_silent_visual_segments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        index = item.get("index") or len(rows) + 1
        confidence = safe_float(item.get("confidence"), 0.0)
        kind = str(item.get("type") or "silent_visual_segment")
        for role, key in [("start", "start"), ("end", "end")]:
            time_value = safe_float(item.get(key), -1.0)
            if time_value < 0:
                continue
            rows.append({
                "source_ref": f"silent_visual_segment_{index}_{role}",
                "source": "silent_visual_segment",
                "time": round(time_value, 3),
                "type": kind,
                "confidence": confidence,
                "reason": f"{role}_of_accepted_silent_visual_segment",
                "evidence_refs": item.get("evidence_refs") or [],
                "raw": item,
            })
    return rows


def normalize_location_transitions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        if not bool(item.get("is_shooting_location_transition")):
            continue
        time_value = safe_float(item.get("time"), -1.0)
        if time_value < 0:
            continue
        source_ref = candidate_ref("scene_cut", item)
        rows.append({
            "source_ref": source_ref,
            "source": "shooting_location_transition",
            "time": round(time_value, 3),
            "type": "shooting_location_transition",
            "confidence": safe_float(item.get("confidence"), 0.0),
            "reason": "confirmed_shooting_location_transition",
            "before_scene_id": item.get("before_scene_id"),
            "after_scene_id": item.get("after_scene_id"),
            "before_scene_label": item.get("before_scene_label"),
            "after_scene_label": item.get("after_scene_label"),
            "contact_sheet": item.get("contact_sheet"),
            "raw": item,
        })
    return rows


def dedupe_candidates(candidates: list[dict[str, Any]], window_seconds: float) -> list[dict[str, Any]]:
    priority = {"shooting_location_transition": 4, "silent_visual_segment": 3, "separator": 2, "visual_boundary": 1}
    ordered = sorted(candidates, key=lambda item: (safe_float(item.get("time")), -priority.get(str(item.get("source")), 0), -safe_float(item.get("confidence"))))
    result: list[dict[str, Any]] = []
    for candidate in ordered:
        time_value = safe_float(candidate.get("time"))
        duplicate_index = None
        for index, existing in enumerate(result):
            if abs(time_value - safe_float(existing.get("time"))) <= window_seconds and candidate.get("source_ref") == existing.get("source_ref"):
                duplicate_index = index
                break
        if duplicate_index is None:
            result.append(candidate)
            continue
        existing = result[duplicate_index]
        if (priority.get(str(candidate.get("source")), 0), safe_float(candidate.get("confidence"))) > (priority.get(str(existing.get("source")), 0), safe_float(existing.get("confidence"))):
            result[duplicate_index] = candidate
    return sorted(result, key=lambda item: safe_float(item.get("time")))


def nearest_distance(time_value: float, boundary_times: list[float]) -> float | None:
    if not boundary_times:
        return None
    return min(abs(time_value - item) for item in boundary_times)


def containing_segment(candidate: dict[str, Any], segments: list[dict[str, Any]]) -> dict[str, Any] | None:
    time_value = safe_float(candidate.get("time"))
    for segment in segments:
        start = safe_float(segment.get("start"))
        end = safe_float(segment.get("end"))
        if start < time_value < end:
            return segment
    return None


def resulting_segment_duration_decision(candidate: dict[str, Any], segments: list[dict[str, Any]], promoted: list[dict[str, Any]], args: argparse.Namespace) -> tuple[bool, str]:
    segment = containing_segment(candidate, segments)
    if segment is None:
        return False, "outside_semantic_segment"

    min_duration = float(args.min_resulting_segment_duration)
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"))
    time_value = safe_float(candidate.get("time"))
    segment_index = int(segment.get("index") or 0)
    existing_cuts = [
        safe_float(item.get("time"))
        for item in promoted
        if int(item.get("semantic_segment_index") or 0) == segment_index
    ]
    points = [start] + sorted(existing_cuts + [time_value]) + [end]
    durations = [round(points[index + 1] - points[index], 3) for index in range(len(points) - 1)]
    shortest = min(durations) if durations else 0.0
    if shortest < min_duration:
        return False, f"resulting_segment_too_short:segment={segment_index},shortest={shortest:.3f}s,min={min_duration:.3f}s"
    return True, f"semantic_segment_{segment_index}_resulting_segments_min_duration_ok"


def promotion_decision(
    candidate: dict[str, Any],
    used_refs: set[str],
    existing_boundary_times: list[float],
    used_alignment: bool,
    segments: list[dict[str, Any]],
    promoted: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[bool, str]:
    source_ref = str(candidate.get("source_ref") or "")
    source = str(candidate.get("source") or "")
    kind = str(candidate.get("type") or "")
    confidence = safe_float(candidate.get("confidence"), 0.0)
    time_value = safe_float(candidate.get("time"))

    if source_ref in used_refs:
        return False, "already_used_by_alignment"

    distance = nearest_distance(time_value, existing_boundary_times)
    if distance is not None and distance <= float(args.dedupe_window_seconds):
        reason = "near_existing_aligned_boundary" if used_alignment else "near_existing_semantic_segment_boundary"
        return False, f"{reason}:{distance:.3f}s"

    if source == "shooting_location_transition":
        if confidence >= float(args.min_location_transition_confidence):
            ok, guard_reason = resulting_segment_duration_decision(candidate, segments, promoted, args)
            if not ok:
                return False, guard_reason
            return True, f"confirmed_shooting_location_transition+{guard_reason}"
        return False, "location_transition_confidence_below_threshold"

    if source == "silent_visual_segment":
        if confidence >= float(args.min_silent_visual_confidence):
            ok, guard_reason = resulting_segment_duration_decision(candidate, segments, promoted, args)
            if not ok:
                return False, guard_reason
            return True, f"accepted_long_silent_visual_segment_boundary+{guard_reason}"
        return False, "silent_visual_confidence_below_threshold"

    if source == "separator":
        if kind in STRONG_SEPARATOR_TYPES and confidence >= float(args.min_separator_confidence):
            ok, guard_reason = resulting_segment_duration_decision(candidate, segments, promoted, args)
            if not ok:
                return False, guard_reason
            return True, f"strong_separator_{kind}+{guard_reason}"
        if kind == "info_insert_candidate" and bool(args.promote_info_inserts) and confidence >= float(args.min_info_insert_confidence):
            ok, guard_reason = resulting_segment_duration_decision(candidate, segments, promoted, args)
            if not ok:
                return False, guard_reason
            return True, f"promoted_info_insert_candidate+{guard_reason}"
        if kind in STRONG_SEPARATOR_TYPES:
            return False, "strong_separator_confidence_below_threshold"
        return False, "separator_type_not_promoted"

    if source == "visual_boundary":
        if bool(args.promote_visual_changes) and confidence >= float(args.min_visual_change_confidence):
            ok, guard_reason = resulting_segment_duration_decision(candidate, segments, promoted, args)
            if not ok:
                return False, guard_reason
            return True, f"promoted_high_confidence_visual_change+{guard_reason}"
        return False, "ordinary_visual_change_not_promoted"

    return False, "unsupported_candidate_source"


def public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "source_ref", "source", "time", "type", "confidence", "reason", "evidence_frame", "evidence_refs",
        "before_scene_id", "after_scene_id", "before_scene_label", "after_scene_label", "contact_sheet",
    ]
    return {key: candidate[key] for key in keys if key in candidate and candidate[key] not in (None, "", [])}


def build_promoted_item(index: int, candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    item = public_candidate(candidate)
    segment = candidate.get("semantic_segment") if isinstance(candidate.get("semantic_segment"), dict) else None
    item.update({
        "id": f"promoted_visual_{index:03d}",
        "source_ref": candidate.get("source_ref"),
        "time": round(safe_float(candidate.get("time")), 3),
        "confidence": safe_float(candidate.get("confidence"), 0.0),
        "promotion_reason": reason,
        "boundary_source": "promoted_visual_boundary",
    })
    if segment is not None:
        item["semantic_segment_index"] = int(segment.get("index") or 0)
        item["semantic_segment_start"] = round(safe_float(segment.get("start")), 3)
        item["semantic_segment_end"] = round(safe_float(segment.get("end")), 3)
    return item


def build_rejected_item(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    item = public_candidate(candidate)
    segment = candidate.get("semantic_segment") if isinstance(candidate.get("semantic_segment"), dict) else None
    item.update({
        "source_ref": candidate.get("source_ref"),
        "time": round(safe_float(candidate.get("time")), 3),
        "confidence": safe_float(candidate.get("confidence"), 0.0),
        "reject_reason": reason,
    })
    if segment is not None:
        item["semantic_segment_index"] = int(segment.get("index") or 0)
        item["semantic_segment_start"] = round(safe_float(segment.get("start")), 3)
        item["semantic_segment_end"] = round(safe_float(segment.get("end")), 3)
    return item


def render_summary(result: dict[str, Any]) -> str:
    counts = result.get("counts") or {}
    lines = [
        "# Visual Boundary Promotion Summary",
        "",
        f"- Tool: {TOOL_NAME} {TOOL_VERSION}",
        f"- Used Boundary Alignment: {result.get('used_boundary_alignment')}",
        f"- Candidates: {counts.get('candidates', 0)}",
        f"- Promoted: {counts.get('promoted', 0)}",
        f"- Rejected: {counts.get('rejected', 0)}",
        "",
    ]
    if result.get("promoted_items"):
        lines.append("## Promoted")
        lines.append("")
        for item in result["promoted_items"]:
            lines.append(f"- {item['id']}: {item['time']}s, {item.get('source')}, {item.get('type')}, reason={item.get('promotion_reason')}")
        lines.append("")
    reject_counts = counts.get("reject_reasons") or {}
    if reject_counts:
        lines.append("## Rejected Reasons")
        lines.append("")
        for reason, count in sorted(reject_counts.items()):
            lines.append(f"- {reason}: {count}")
        lines.append("")
    return "\n".join(lines)


def run_promoter(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    segments = load_required_semantic_segments(paths.meta_dir)
    used_alignment, used_refs, aligned_times = load_alignment_context(paths.meta_dir, bool(args.ignore_boundary_alignment))
    existing_boundary_times = aligned_times if used_alignment else segment_boundary_times(segments)

    candidates = []
    candidates.extend(normalize_location_transitions(optional_items(paths.meta_dir, "shooting_location_transition_candidates.json")))
    candidates.extend(normalize_silent_visual_segments(optional_items(paths.meta_dir, "silent_visual_segments.json")))
    candidates.extend(normalize_separator_candidates(optional_items(paths.meta_dir, "separator_candidates.json")))
    candidates.extend(normalize_visual_candidates(optional_items(paths.meta_dir, "visual_boundary_candidates.json")))
    candidates = dedupe_candidates(candidates, float(args.candidate_dedupe_window_seconds))

    promoted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reject_reasons: dict[str, int] = {}
    for candidate in candidates:
        segment = containing_segment(candidate, segments)
        if segment is not None:
            candidate["semantic_segment"] = segment
        should_promote, reason = promotion_decision(candidate, used_refs, existing_boundary_times, used_alignment, segments, promoted, args)
        if should_promote:
            promoted.append(build_promoted_item(len(promoted) + 1, candidate, reason))
        else:
            rejected.append(build_rejected_item(candidate, reason))
            reason_key = reason.split(":", 1)[0]
            reject_reasons[reason_key] = reject_reasons.get(reason_key, 0) + 1

    common = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workspace": str(paths.workspace) if paths.workspace else "",
        "used_boundary_alignment": used_alignment,
        "dependency_status": {
            "semantic_segment_candidates": "required_present",
            "boundary_alignment": "used" if used_alignment else "not_used",
        },
        "parameters": {
            "dedupe_window_seconds": float(args.dedupe_window_seconds),
            "candidate_dedupe_window_seconds": float(args.candidate_dedupe_window_seconds),
            "min_separator_confidence": float(args.min_separator_confidence),
            "min_location_transition_confidence": float(args.min_location_transition_confidence),
            "min_silent_visual_confidence": float(args.min_silent_visual_confidence),
            "min_resulting_segment_duration": float(args.min_resulting_segment_duration),
            "promote_info_inserts": bool(args.promote_info_inserts),
            "promote_visual_changes": bool(args.promote_visual_changes),
        },
    }
    promoted_payload = {**common, "items": promoted}
    rejected_payload = {**common, "items": rejected}
    result = {
        **common,
        "status": "completed",
        "outputs": {
            "promoted_visual_boundaries": str(paths.meta_dir / "promoted_visual_boundaries.json"),
            "rejected_visual_boundaries": str(paths.meta_dir / "rejected_visual_boundaries.json"),
            "summary": str(paths.meta_dir / "visual_boundary_promotion_summary.md"),
        },
        "counts": {
            "semantic_segments": len(segments),
            "existing_boundary_times": len(existing_boundary_times),
            "used_alignment_refs": len(used_refs),
            "candidates": len(candidates),
            "promoted": len(promoted),
            "rejected": len(rejected),
            "reject_reasons": reject_reasons,
        },
        "promoted_items": promoted,
    }
    write_json(paths.meta_dir / "promoted_visual_boundaries.json", promoted_payload)
    write_json(paths.meta_dir / "rejected_visual_boundaries.json", rejected_payload)
    write_text(paths.meta_dir / "visual_boundary_promotion_summary.md", render_summary(result))
    write_json(paths.meta_dir / "09_visual_boundary_promoter_result.json", {key: value for key, value in result.items() if key != "promoted_items"})
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
        "optional_dependencies": ["08 boundary_alignment.json", "07 silent_visual_segments.json", "06 shooting_location_transition_candidates.json"],
    }
    write_json(paths.meta_dir / "09_visual_boundary_promoter_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Promote strong visual boundaries to structural boundary candidates without modifying the final timeline.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--ignore-boundary-alignment", action="store_true", help="Ignore optional 08 boundary_alignment.json and fall back to 03 segment boundaries.")
    parser.add_argument("--dedupe-window-seconds", type=float, default=0.5, help="Reject candidates this close to existing semantic/aligned boundaries.")
    parser.add_argument("--candidate-dedupe-window-seconds", type=float, default=0.15)
    parser.add_argument("--min-separator-confidence", type=float, default=0.75)
    parser.add_argument("--min-info-insert-confidence", type=float, default=0.8)
    parser.add_argument("--min-location-transition-confidence", type=float, default=0.7)
    parser.add_argument("--min-silent-visual-confidence", type=float, default=0.75)
    parser.add_argument("--min-visual-change-confidence", type=float, default=0.9)
    parser.add_argument("--min-resulting-segment-duration", type=float, default=3.0, help="Reject promoted boundaries that would split any 03 semantic segment into a sub-segment shorter than this many seconds.")
    parser.add_argument("--promote-info-inserts", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--promote-visual-changes", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None)
    try:
        result = run_promoter(paths, args)
        exit_code = 0
    except DependencyError as exc:
        result = failed_result(paths, str(exc))
        exit_code = 2
    if args.print_json:
        print(json.dumps({key: value for key, value in result.items() if key != "promoted_items"}, ensure_ascii=False, indent=2))
    elif result.get("status") == "completed":
        print(f"{TOOL_NAME} completed: {result['outputs']['promoted_visual_boundaries']}")
    else:
        print(f"{TOOL_NAME} failed: {result.get('message')}")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
