from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "SilentVisualSegmentDetector"
TOOL_VERSION = "0.1.0"


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


def load_video_duration(meta_dir: Path, asr: dict[str, Any]) -> float:
    metadata_path = meta_dir / "video_metadata.json"
    if metadata_path.exists():
        try:
            return float(read_json(metadata_path).get("duration_seconds") or 0.0)
        except Exception:
            pass
    candidates = [float(item.get("end") or 0.0) for item in asr.get("segments") or [] if isinstance(item, dict)]
    candidates.extend(float(item.get("time") or 0.0) for item in optional_items(meta_dir, "visual_boundary_candidates.json"))
    return max(candidates + [0.0])


def clamp(value: float, start: float, end: float) -> float:
    return min(max(value, start), end)


def compute_silent_ranges(asr_segments: list[dict[str, Any]], duration: float, min_silent_duration: float, edge_padding: float) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    previous_end = 0.0
    for item in sorted(asr_segments, key=lambda row: float(row.get("start") or 0.0)):
        start = clamp(float(item.get("start") or 0.0), 0.0, duration)
        end = clamp(float(item.get("end") or start), start, duration)
        silent_start = clamp(previous_end + edge_padding, 0.0, duration)
        silent_end = clamp(start - edge_padding, 0.0, duration)
        if silent_end - silent_start >= min_silent_duration:
            ranges.append({"start": round(silent_start, 3), "end": round(silent_end, 3), "duration": round(silent_end - silent_start, 3)})
        previous_end = max(previous_end, end)

    silent_start = clamp(previous_end + edge_padding, 0.0, duration)
    silent_end = duration
    if silent_end - silent_start >= min_silent_duration:
        ranges.append({"start": round(silent_start, 3), "end": round(silent_end, 3), "duration": round(silent_end - silent_start, 3)})
    return ranges


def in_range(item: dict[str, Any], start: float, end: float, margin: float = 0.0) -> bool:
    try:
        t = float(item.get("time") or 0.0)
    except (TypeError, ValueError):
        return False
    return start - margin <= t <= end + margin


def nearby_keyframes(keyframes: list[dict[str, Any]], start: float, end: float, limit: int) -> list[dict[str, Any]]:
    midpoint = (start + end) / 2.0
    scoped = []
    for item in keyframes:
        try:
            time_value = float(item.get("time") or 0.0)
        except (TypeError, ValueError):
            continue
        if start - 0.75 <= time_value <= end + 0.75 and item.get("path"):
            scoped.append({
                "time": round(time_value, 3),
                "role": item.get("role") or "evidence",
                "source": item.get("source") or item.get("segment_source") or "keyframe",
                "path": item.get("path"),
                "distance_to_midpoint": round(abs(time_value - midpoint), 3),
            })
    scoped.sort(key=lambda item: (float(item["distance_to_midpoint"]), float(item["time"])))
    return scoped[:limit]


def evidence_refs(*groups: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    for group in groups:
        for item in group:
            for key in ["evidence_frame", "path"]:
                value = item.get(key)
                if value and str(value) not in refs:
                    refs.append(str(value))
    return refs


def classify_range(duration: float, visual_count: int, cut_count: int, separator_types: list[str]) -> str:
    if any(kind in {"black_screen", "white_screen", "solid_color_separator", "title_card", "title_card_candidate", "chapter_card"} for kind in separator_types):
        return "separator_or_title_card"
    if duration >= 8.0 and (visual_count >= 2 or cut_count >= 2):
        return "visual_sequence_or_showcase"
    if cut_count > 0:
        return "visual_transition"
    if visual_count > 0:
        return "visual_change_during_silence"
    return "silent_gap_without_visual_structure"


def confidence_for_range(duration: float, visual: list[dict[str, Any]], cuts: list[dict[str, Any]], separators: list[dict[str, Any]]) -> float:
    visual_score = max([float(item.get("confidence") or 0.0) for item in visual] + [0.0])
    cut_score = max([float(item.get("confidence") or 0.0) for item in cuts] + [0.0])
    separator_score = max([float(item.get("confidence") or 0.0) for item in separators] + [0.0])
    evidence_bonus = min(0.16, 0.04 * (len(visual) + len(cuts) + len(separators)))
    duration_bonus = 0.08 if duration >= 8.0 else 0.04 if duration >= 4.0 else 0.0
    return round(min(0.95, max(visual_score, cut_score, separator_score) + evidence_bonus + duration_bonus), 3)


def detect_silent_visual_segments(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    asr_path = paths.meta_dir / "asr_segments.json"
    if not asr_path.exists():
        raise FileNotFoundError(f"missing ASR input: {asr_path}")
    asr = read_json(asr_path)
    asr_segments = [item for item in (asr.get("segments") or []) if isinstance(item, dict)]
    duration = load_video_duration(paths.meta_dir, asr)
    silent_ranges = compute_silent_ranges(asr_segments, duration, float(args.min_silent_duration), float(args.edge_padding_seconds))

    visual = optional_items(paths.meta_dir, "visual_boundary_candidates.json")
    separators = optional_items(paths.meta_dir, "separator_candidates.json")
    cuts = optional_items(paths.meta_dir, "pyscenedetect_cuts.json", "cuts") or optional_items(paths.meta_dir, "pyscenedetect_cuts.json")
    keyframes = optional_items(paths.meta_dir, "visual_keyframes.json")

    items: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for silent_index, silent in enumerate(silent_ranges, start=1):
        start = float(silent["start"])
        end = float(silent["end"])
        scoped_visual = [item for item in visual if in_range(item, start, end) and float(item.get("confidence") or 0.0) >= float(args.min_visual_confidence)]
        scoped_cuts = [item for item in cuts if in_range(item, start, end, margin=float(args.cut_margin_seconds)) and float(item.get("confidence") or 0.0) >= float(args.min_cut_confidence)]
        scoped_separators = [item for item in separators if in_range(item, start, end) and float(item.get("confidence") or 0.0) >= float(args.min_separator_confidence)]
        has_visual_structure = bool(scoped_visual or scoped_cuts or scoped_separators)
        frame_refs = nearby_keyframes(keyframes, start, end, int(args.max_keyframes_per_segment))
        separator_types = sorted({str(item.get("type") or "") for item in scoped_separators if item.get("type")})
        reason_parts = []
        if scoped_visual:
            reason_parts.append("visual_change_inside_silent_range")
        if scoped_cuts:
            reason_parts.append("scene_cut_inside_silent_range")
        if scoped_separators:
            reason_parts.append("separator_inside_silent_range")
        if not reason_parts:
            reason_parts.append("silent_range_without_required_visual_evidence")

        row = {
            "index": silent_index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "type": classify_range(end - start, len(scoped_visual), len(scoped_cuts), separator_types),
            "confidence": confidence_for_range(end - start, scoped_visual, scoped_cuts, scoped_separators) if has_visual_structure else 0.0,
            "reason": "+".join(reason_parts),
            "visual_boundary_count": len(scoped_visual),
            "scene_cut_count": len(scoped_cuts),
            "separator_count": len(scoped_separators),
            "separator_types": separator_types,
            "visual_boundary_refs": [item.get("index") for item in scoped_visual],
            "scene_cut_refs": [item.get("index") for item in scoped_cuts],
            "separator_refs": [item.get("index") for item in scoped_separators],
            "keyframes": frame_refs,
            "evidence_refs": evidence_refs(scoped_visual, scoped_separators, frame_refs),
        }
        if has_visual_structure or not bool(args.strong_visual_change_required):
            items.append(row)
        else:
            rejected.append(row)

    common = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "duration_seconds": round(duration, 3),
        "parameters": {
            "min_silent_duration": float(args.min_silent_duration),
            "strong_visual_change_required": bool(args.strong_visual_change_required),
            "min_visual_confidence": float(args.min_visual_confidence),
            "min_cut_confidence": float(args.min_cut_confidence),
            "min_separator_confidence": float(args.min_separator_confidence),
            "edge_padding_seconds": float(args.edge_padding_seconds),
        },
    }
    write_json(paths.meta_dir / "silent_visual_segments.json", {**common, "items": items, "rejected_items": rejected})
    write_text(paths.meta_dir / "silent_visual_segments_summary.md", render_summary(items, rejected, common))
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace": str(paths.workspace) if paths.workspace else "",
        "outputs": {
            "silent_visual_segments": str(paths.meta_dir / "silent_visual_segments.json"),
            "summary": str(paths.meta_dir / "silent_visual_segments_summary.md"),
        },
        "counts": {
            "silent_ranges": len(silent_ranges),
            "silent_visual_segments": len(items),
            "rejected_silent_ranges": len(rejected),
        },
    }
    write_json(paths.meta_dir / "07_silent_visual_segment_detector_result.json", result)
    return result


def render_summary(items: list[dict[str, Any]], rejected: list[dict[str, Any]], common: dict[str, Any]) -> str:
    lines = [
        "# Silent Visual Segment Summary",
        "",
        f"- Tool: {TOOL_NAME} {TOOL_VERSION}",
        f"- Duration Seconds: {common.get('duration_seconds')}",
        f"- Accepted Segments: {len(items)}",
        f"- Rejected Silent Ranges: {len(rejected)}",
        "",
    ]
    for item in items:
        lines.append(f"- Segment {item['index']}: {item['start']}s - {item['end']}s ({item['duration']}s), {item['type']}, confidence={item['confidence']}, reason={item['reason']}")
    if rejected:
        lines.extend(["", "## Rejected", ""])
        for item in rejected:
            lines.append(f"- Range {item['index']}: {item['start']}s - {item['end']}s ({item['duration']}s), reason={item['reason']}")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect silent visual segments from ASR gaps and visual evidence.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--min-silent-duration", type=float, default=2.0, help="Minimum ASR-free interval in seconds.")
    parser.add_argument("--strong-visual-change-required", action=argparse.BooleanOptionalAction, default=True, help="Require visual/cut/separator evidence inside the silent range.")
    parser.add_argument("--min-visual-confidence", type=float, default=0.72)
    parser.add_argument("--min-cut-confidence", type=float, default=0.7)
    parser.add_argument("--min-separator-confidence", type=float, default=0.62)
    parser.add_argument("--edge-padding-seconds", type=float, default=0.15, help="Trim this many seconds from speech boundaries before evaluating silence.")
    parser.add_argument("--cut-margin-seconds", type=float, default=0.2, help="Allow nearby scene cuts at the edge of a silent interval.")
    parser.add_argument("--max-keyframes-per-segment", type=int, default=6)
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None)
    result = detect_silent_visual_segments(paths, args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} completed: {result['outputs']['silent_visual_segments']}")


if __name__ == "__main__":
    main()
