from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "OverFragmentedSegmentMerger"
TOOL_VERSION = "0.1.0"

PROTECTED_BOUNDARY_TYPES = {
    "prompt_anchor",
    "problem_to_solution",
    "question_to_answer",
    "setup_to_turning_point",
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
            "OverFragmentedSegmentMerger requires 03 SemanticLLMStructureBuilder output: "
            f"{path}. Run 03_semantic_llm_structure_builder.py before 12, or point --output-dir to a meta directory containing semantic_segment_candidates.json."
        )
    payload = read_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise DependencyError(f"Invalid 03 dependency output: {path} must contain an items list.")
    segments = [item for item in items if isinstance(item, dict)]
    if not segments:
        raise DependencyError(f"Invalid 03 dependency output: {path} has no semantic segment items.")
    return sorted(segments, key=lambda item: safe_float(item.get("start")))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def segment_duration(segment: dict[str, Any]) -> float:
    return max(0.0, safe_float(segment.get("end")) - safe_float(segment.get("start")))


def dialogue_chars(segment: dict[str, Any]) -> int:
    return len(str(segment.get("dialogue_text") or ""))


def load_alignment_boundaries(meta_dir: Path) -> tuple[bool, list[dict[str, Any]]]:
    path = meta_dir / "boundary_alignment.json"
    if not path.exists():
        return False, []
    payload = read_json(path)
    rows: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "time": round(safe_float(item.get("aligned_time"), safe_float(item.get("original_time"))), 3),
            "source": "boundary_alignment",
            "source_ref": item.get("semantic_boundary_id"),
            "type": item.get("semantic_boundary_type") or "",
            "confidence": safe_float(item.get("confidence"), 0.0),
            "alignment_type": item.get("alignment_type") or "",
            "snap_strength": item.get("snap_strength") or "",
        })
    return True, rows


def load_promoted_boundaries(meta_dir: Path) -> tuple[bool, list[dict[str, Any]]]:
    path = meta_dir / "promoted_visual_boundaries.json"
    if not path.exists():
        return False, []
    rows = []
    for item in optional_items(meta_dir, "promoted_visual_boundaries.json"):
        rows.append({
            "time": round(safe_float(item.get("time")), 3),
            "source": "promoted_visual_boundary",
            "source_ref": item.get("id") or item.get("source_ref"),
            "type": item.get("type") or item.get("source") or "",
            "confidence": safe_float(item.get("confidence"), 0.0),
        })
    return True, rows


def nearby_boundaries(time_value: float, boundaries: list[dict[str, Any]], window: float) -> list[dict[str, Any]]:
    rows = []
    for item in boundaries:
        distance = abs(safe_float(item.get("time")) - time_value)
        if distance <= window:
            row = dict(item)
            row["distance_seconds"] = round(distance, 3)
            rows.append(row)
    return sorted(rows, key=lambda item: safe_float(item.get("distance_seconds")))


def boundary_is_protected(time_value: float, boundaries: list[dict[str, Any]], args: argparse.Namespace) -> tuple[bool, str, list[dict[str, Any]]]:
    nearby = nearby_boundaries(time_value, boundaries, float(args.boundary_match_window))
    for item in nearby:
        if item.get("source") == "promoted_visual_boundary":
            return True, "protected_promoted_visual_boundary", nearby
        if str(item.get("type") or "") in PROTECTED_BOUNDARY_TYPES:
            return True, f"protected_semantic_boundary_type:{item.get('type')}", nearby
    return False, "not_protected", nearby


def gap_between(left: dict[str, Any], right: dict[str, Any]) -> float:
    return max(0.0, safe_float(right.get("start")) - safe_float(left.get("end")))


def same_formula_slot(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return str(left.get("formula_slot") or "") == str(right.get("formula_slot") or "")


def merge_candidate_score(short_segment: dict[str, Any], neighbor: dict[str, Any], gap: float, args: argparse.Namespace) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if gap <= float(args.max_merge_gap_seconds):
        score += 3
        reasons.append("gap_within_merge_threshold")
    if same_formula_slot(short_segment, neighbor):
        score += 2
        reasons.append("same_formula_slot")
    if dialogue_chars(short_segment) < int(args.min_dialogue_chars):
        score += 2
        reasons.append("short_dialogue")
    if segment_duration(short_segment) < float(args.min_segment_duration):
        score += 3
        reasons.append("short_duration")
    if str(short_segment.get("semantic_role") or "") in {"导流结尾", "平台导流", "收尾"}:
        score += 1
        reasons.append("tail_or_outro_fragment")
    return score, reasons


def choose_merge_target(index: int, segments: list[dict[str, Any]], boundaries: list[dict[str, Any]], args: argparse.Namespace) -> tuple[str, dict[str, Any] | None, list[str], list[dict[str, Any]]]:
    segment = segments[index]
    options: list[tuple[int, str, dict[str, Any], list[str], list[dict[str, Any]]]] = []
    if index > 0:
        left = segments[index - 1]
        boundary_time = safe_float(segment.get("start"))
        protected, protected_reason, nearby = boundary_is_protected(boundary_time, boundaries, args)
        if not protected:
            score, reasons = merge_candidate_score(segment, left, gap_between(left, segment), args)
            options.append((score, "merge_with_previous", left, reasons, nearby))
        else:
            options.append((-1, f"blocked_previous:{protected_reason}", left, [], nearby))
    if index + 1 < len(segments):
        right = segments[index + 1]
        boundary_time = safe_float(segment.get("end"))
        protected, protected_reason, nearby = boundary_is_protected(boundary_time, boundaries, args)
        if not protected:
            score, reasons = merge_candidate_score(segment, right, gap_between(segment, right), args)
            options.append((score, "merge_with_next", right, reasons, nearby))
        else:
            options.append((-1, f"blocked_next:{protected_reason}", right, [], nearby))
    valid = [item for item in options if item[0] >= int(args.min_merge_score)]
    if not valid:
        blocked = [item for item in options if item[0] < 0]
        if blocked:
            return blocked[0][1], blocked[0][2], [], blocked[0][4]
        return "no_safe_merge_target", None, [], []
    valid.sort(key=lambda item: item[0], reverse=True)
    best = valid[0]
    return best[1], best[2], best[3], best[4]


def merged_range(segment: dict[str, Any], target: dict[str, Any] | None) -> dict[str, Any]:
    if target is None:
        return {}
    start = min(safe_float(segment.get("start")), safe_float(target.get("start")))
    end = max(safe_float(segment.get("end")), safe_float(target.get("end")))
    return {"start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)}


def dependency_status(used_alignment: bool, used_promoted: bool) -> dict[str, str]:
    return {
        "semantic_segment_candidates": "required_present",
        "boundary_alignment": "used" if used_alignment else "missing",
        "promoted_visual_boundaries": "used" if used_promoted else "missing",
    }


def render_summary(payload: dict[str, Any]) -> str:
    counts = payload.get("counts") or {}
    lines = [
        "# Over-Fragmented Merge Summary",
        "",
        f"- Tool: {TOOL_NAME} {TOOL_VERSION}",
        f"- Semantic Segments: {counts.get('semantic_segments', 0)}",
        f"- Merge Suggestions: {counts.get('merge_suggestions', 0)}",
        "",
    ]
    for item in payload.get("items") or []:
        if item.get("action") != "merge_suggested":
            continue
        target = item.get("target_segment_index")
        lines.append(f"- Segment {item['segment_index']} -> {item['merge_direction']} segment {target}, merged_range={item.get('merged_range')}, reasons={','.join(item.get('merge_reasons') or [])}")
    lines.append("")
    return "\n".join(lines)


def run_merger(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    segments = load_required_semantic_segments(paths.meta_dir)
    used_alignment, alignment_boundaries = load_alignment_boundaries(paths.meta_dir)
    used_promoted, promoted_boundaries = load_promoted_boundaries(paths.meta_dir)
    boundaries = alignment_boundaries + promoted_boundaries

    items: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        duration = segment_duration(segment)
        chars = dialogue_chars(segment)
        is_fragment = duration < float(args.min_segment_duration) or chars < int(args.min_dialogue_chars)
        item = {
            "segment_index": int(segment.get("index") or index + 1),
            "segment_title": segment.get("title") or "",
            "segment_start": round(safe_float(segment.get("start")), 3),
            "segment_end": round(safe_float(segment.get("end")), 3),
            "segment_duration": round(duration, 3),
            "dialogue_chars": chars,
            "is_fragment_candidate": is_fragment,
            "action": "no_action",
            "reason": "segment_not_fragmented",
        }
        if is_fragment:
            direction, target, reasons, nearby = choose_merge_target(index, segments, boundaries, args)
            if direction in {"merge_with_previous", "merge_with_next"} and target is not None:
                item.update({
                    "action": "merge_suggested",
                    "reason": "overfragmented_segment_merge_suggested",
                    "merge_direction": direction,
                    "target_segment_index": int(target.get("index") or 0),
                    "target_segment_title": target.get("title") or "",
                    "merge_reasons": reasons,
                    "merged_range": merged_range(segment, target),
                    "nearby_boundary_evidence": nearby,
                })
            else:
                item.update({
                    "action": "merge_rejected",
                    "reason": direction,
                    "nearby_boundary_evidence": nearby,
                })
        items.append(item)

    suggestions = [item for item in items if item.get("action") == "merge_suggested"]
    payload = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workspace": str(paths.workspace) if paths.workspace else "",
        "mode": "suggestions_only",
        "dependency_status": dependency_status(used_alignment, used_promoted),
        "parameters": {
            "min_segment_duration": float(args.min_segment_duration),
            "min_dialogue_chars": int(args.min_dialogue_chars),
            "max_merge_gap_seconds": float(args.max_merge_gap_seconds),
            "boundary_match_window": float(args.boundary_match_window),
            "min_merge_score": int(args.min_merge_score),
        },
        "counts": {
            "semantic_segments": len(segments),
            "alignment_boundaries": len(alignment_boundaries),
            "promoted_boundaries": len(promoted_boundaries),
            "fragment_candidates": sum(1 for item in items if item.get("is_fragment_candidate")),
            "merge_suggestions": len(suggestions),
        },
        "items": items,
    }
    write_json(paths.meta_dir / "overfragmented_merge_decision.json", payload)
    write_text(paths.meta_dir / "overfragmented_merge_summary.md", render_summary(payload))
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace": str(paths.workspace) if paths.workspace else "",
        "mode": "suggestions_only",
        "outputs": {
            "overfragmented_merge_decision": str(paths.meta_dir / "overfragmented_merge_decision.json"),
            "summary": str(paths.meta_dir / "overfragmented_merge_summary.md"),
        },
        "counts": payload["counts"],
        "dependency_status": payload["dependency_status"],
    }
    write_json(paths.meta_dir / "12_overfragmented_segment_merger_result.json", result)
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
    write_json(paths.meta_dir / "12_overfragmented_segment_merger_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Suggest merges for over-fragmented semantic segments without modifying the timeline.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--min-segment-duration", type=float, default=3.0)
    parser.add_argument("--min-dialogue-chars", type=int, default=8)
    parser.add_argument("--max-merge-gap-seconds", type=float, default=2.0)
    parser.add_argument("--boundary-match-window", type=float, default=0.5)
    parser.add_argument("--min-merge-score", type=int, default=4)
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None)
    try:
        result = run_merger(paths, args)
        exit_code = 0
    except DependencyError as exc:
        result = failed_result(paths, str(exc))
        exit_code = 2
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("status") == "completed":
        print(f"{TOOL_NAME} completed: {result['outputs']['overfragmented_merge_decision']}")
    else:
        print(f"{TOOL_NAME} failed: {result.get('message')}")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
