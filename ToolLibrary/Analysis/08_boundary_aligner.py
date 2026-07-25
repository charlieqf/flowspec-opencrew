from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "BoundaryAligner"
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


def required_items(meta_dir: Path, filename: str, key: str = "items") -> list[dict[str, Any]]:
    path = meta_dir / filename
    if not path.exists():
        raise FileNotFoundError(f"missing required input: {path}")
    payload = read_json(path)
    value = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(value, list):
        raise ValueError(f"{path} must contain a list field named {key}")
    return [item for item in value if isinstance(item, dict)]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def evidence_ref(prefix: str, item: dict[str, Any]) -> str:
    raw_index = item.get("index")
    if raw_index is not None:
        return f"{prefix}_{raw_index}"
    frame = item.get("frame")
    time_value = safe_float(item.get("time"))
    if frame is not None:
        return f"{prefix}_frame_{frame}"
    return f"{prefix}_t{time_value:.3f}"


def normalize_scene_cuts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        time_value = safe_float(item.get("time"), -1.0)
        if time_value < 0:
            continue
        normalized.append({
            "id": evidence_ref("scene_cut", item),
            "source": "pyscenedetect_cut",
            "alignment_type": "snap_to_scene_cut",
            "time": round(time_value, 3),
            "confidence": safe_float(item.get("confidence"), 0.7),
            "priority": 2,
            "reason": item.get("reason") or "pyscenedetect_cut",
            "raw_index": item.get("index"),
            "frame": item.get("frame"),
        })
    return normalized


def normalize_visual_boundaries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        time_value = safe_float(item.get("time"), -1.0)
        if time_value < 0:
            continue
        normalized.append({
            "id": evidence_ref("visual_boundary", item),
            "source": "visual_boundary",
            "alignment_type": "snap_to_visual_boundary",
            "time": round(time_value, 3),
            "confidence": safe_float(item.get("confidence"), 0.0),
            "priority": 1,
            "reason": item.get("reason") or item.get("type") or "visual_boundary",
            "raw_index": item.get("index"),
            "frame": item.get("frame"),
            "type": item.get("type"),
            "evidence_frame": item.get("evidence_frame"),
        })
    return normalized


def separator_priority(kind: str) -> int:
    strong_types = {"black_screen", "white_screen", "solid_color_separator", "title_card", "chapter_card"}
    return 4 if kind in strong_types else 3


def normalize_separators(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        time_value = safe_float(item.get("time"), -1.0)
        if time_value < 0:
            continue
        kind = str(item.get("type") or "separator_candidate")
        normalized.append({
            "id": evidence_ref("separator", item),
            "source": "separator",
            "alignment_type": "snap_to_separator",
            "time": round(time_value, 3),
            "confidence": safe_float(item.get("confidence"), 0.0),
            "priority": separator_priority(kind),
            "reason": item.get("reason") or kind,
            "raw_index": item.get("index"),
            "frame": item.get("frame"),
            "type": kind,
            "evidence_frame": item.get("evidence_frame"),
        })
    return normalized


def dedupe_evidence(items: list[dict[str, Any]], window_seconds: float) -> list[dict[str, Any]]:
    ordered = sorted(items, key=lambda item: (safe_float(item.get("time")), -int(item.get("priority") or 0), -safe_float(item.get("confidence"))))
    result: list[dict[str, Any]] = []
    for item in ordered:
        time_value = safe_float(item.get("time"))
        duplicate_index = None
        for index, existing in enumerate(result):
            if abs(time_value - safe_float(existing.get("time"))) <= window_seconds:
                duplicate_index = index
                break
        if duplicate_index is None:
            result.append(item)
            continue
        existing = result[duplicate_index]
        item_rank = (int(item.get("priority") or 0), safe_float(item.get("confidence")), -abs(time_value - safe_float(existing.get("time"))))
        existing_rank = (int(existing.get("priority") or 0), safe_float(existing.get("confidence")), 0.0)
        if item_rank > existing_rank:
            merged = dict(item)
            merged["merged_evidence_refs"] = sorted(set((existing.get("merged_evidence_refs") or []) + [str(existing.get("id"))]))
            result[duplicate_index] = merged
        else:
            refs = list(existing.get("merged_evidence_refs") or [])
            refs.append(str(item.get("id")))
            existing["merged_evidence_refs"] = sorted(set(refs))
    return sorted(result, key=lambda item: safe_float(item.get("time")))


def nearby_candidates(boundary_time: float, evidence: list[dict[str, Any]], max_window: float, used_ids: set[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in evidence:
        item_id = str(item.get("id") or "")
        if item_id in used_ids:
            continue
        distance = abs(safe_float(item.get("time")) - boundary_time)
        if distance <= max_window:
            row = dict(item)
            row["distance_seconds"] = round(distance, 3)
            candidates.append(row)
    return candidates


def is_snap_eligible(candidate: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str]:
    distance = safe_float(candidate.get("distance_seconds"))
    confidence = safe_float(candidate.get("confidence"))
    if distance <= float(args.strong_snap_window):
        return True, "strong_snap"
    if distance <= float(args.weak_snap_window) and confidence >= float(args.weak_snap_min_confidence):
        return True, "weak_snap"
    return False, "evidence_only"


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, float, int, float]:
    tier = 2 if candidate.get("snap_strength") == "strong_snap" else 1 if candidate.get("snap_strength") == "weak_snap" else 0
    return (tier, -safe_float(candidate.get("distance_seconds")), int(candidate.get("priority") or 0), safe_float(candidate.get("confidence")))


def alignment_confidence(boundary: dict[str, Any], candidate: dict[str, Any], args: argparse.Namespace) -> float:
    semantic_confidence = safe_float(boundary.get("confidence"), 0.75)
    visual_confidence = safe_float(candidate.get("confidence"), 0.0)
    distance = safe_float(candidate.get("distance_seconds"))
    window = float(args.weak_snap_window) if candidate.get("snap_strength") == "weak_snap" else float(args.strong_snap_window)
    distance_score = max(0.0, 1.0 - (distance / max(window, 0.001)))
    priority_bonus = min(0.05, max(0, int(candidate.get("priority") or 0) - 1) * 0.015)
    return round(min(0.98, semantic_confidence * 0.5 + visual_confidence * 0.35 + distance_score * 0.12 + priority_bonus), 3)


def format_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    fields = ["id", "source", "time", "confidence", "distance_seconds", "type", "reason", "evidence_frame", "merged_evidence_refs"]
    return {key: candidate[key] for key in fields if key in candidate and candidate[key] not in (None, "")}


def align_boundary(boundary: dict[str, Any], evidence: list[dict[str, Any]], used_ids: set[str], args: argparse.Namespace) -> dict[str, Any]:
    boundary_id = str(boundary.get("id") or f"semantic_boundary_t{safe_float(boundary.get('time')):.3f}")
    original_time = round(safe_float(boundary.get("time")), 3)
    candidates = nearby_candidates(original_time, evidence, float(args.evidence_window), used_ids if not bool(args.allow_evidence_reuse) else set())
    enriched: list[dict[str, Any]] = []
    for candidate in candidates:
        eligible, snap_strength = is_snap_eligible(candidate, args)
        row = dict(candidate)
        row["snap_eligible"] = eligible
        row["snap_strength"] = snap_strength
        enriched.append(row)

    snap_candidates = [item for item in enriched if bool(item.get("snap_eligible"))]
    snap_candidates.sort(key=candidate_sort_key, reverse=True)
    evidence_candidates = sorted(enriched, key=lambda item: (safe_float(item.get("distance_seconds")), -safe_float(item.get("confidence"))))[: int(args.max_evidence_per_boundary)]

    if snap_candidates:
        selected = snap_candidates[0]
        selected_id = str(selected.get("id") or "")
        if selected_id:
            used_ids.add(selected_id)
        return {
            "semantic_boundary_id": boundary_id,
            "original_time": original_time,
            "aligned_time": round(safe_float(selected.get("time")), 3),
            "time_shift_seconds": round(safe_float(selected.get("time")) - original_time, 3),
            "alignment_type": selected.get("alignment_type") or "snap_to_visual_evidence",
            "snap_strength": selected.get("snap_strength"),
            "semantic_boundary_type": boundary.get("type"),
            "semantic_confidence": safe_float(boundary.get("confidence"), 0.0),
            "confidence": alignment_confidence(boundary, selected, args),
            "visual_evidence": [selected_id] + list(selected.get("merged_evidence_refs") or []),
            "selected_evidence": format_candidate(selected),
            "nearby_evidence": [format_candidate(item) for item in evidence_candidates],
            "reason": f"{selected.get('snap_strength')} to {selected.get('source')} within {selected.get('distance_seconds')}s",
        }

    if evidence_candidates:
        return {
            "semantic_boundary_id": boundary_id,
            "original_time": original_time,
            "aligned_time": original_time,
            "time_shift_seconds": 0.0,
            "alignment_type": "evidence_only",
            "snap_strength": "none",
            "semantic_boundary_type": boundary.get("type"),
            "semantic_confidence": safe_float(boundary.get("confidence"), 0.0),
            "confidence": safe_float(boundary.get("confidence"), 0.0),
            "visual_evidence": [str(item.get("id")) for item in evidence_candidates if item.get("id")],
            "nearby_evidence": [format_candidate(item) for item in evidence_candidates],
            "reason": "visual evidence found within evidence window but not eligible for snapping",
        }

    return {
        "semantic_boundary_id": boundary_id,
        "original_time": original_time,
        "aligned_time": original_time,
        "time_shift_seconds": 0.0,
        "alignment_type": "no_visual_evidence",
        "snap_strength": "none",
        "semantic_boundary_type": boundary.get("type"),
        "semantic_confidence": safe_float(boundary.get("confidence"), 0.0),
        "confidence": safe_float(boundary.get("confidence"), 0.0),
        "visual_evidence": [],
        "nearby_evidence": [],
        "reason": "no visual evidence within evidence window",
    }


def render_summary(payload: dict[str, Any]) -> str:
    counts = payload.get("counts") or {}
    lines = [
        "# Boundary Alignment Summary",
        "",
        f"- Tool: {TOOL_NAME} {TOOL_VERSION}",
        f"- Semantic Boundaries: {counts.get('semantic_boundaries', 0)}",
        f"- Strong Snaps: {counts.get('strong_snaps', 0)}",
        f"- Weak Snaps: {counts.get('weak_snaps', 0)}",
        f"- Evidence Only: {counts.get('evidence_only', 0)}",
        f"- No Visual Evidence: {counts.get('no_visual_evidence', 0)}",
        "",
    ]
    for item in payload.get("items") or []:
        lines.append(
            f"- {item['semantic_boundary_id']}: {item['original_time']}s -> {item['aligned_time']}s, "
            f"{item['alignment_type']}, confidence={item['confidence']}, reason={item['reason']}"
        )
    lines.append("")
    return "\n".join(lines)


def run_aligner(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    semantic_boundaries = required_items(paths.meta_dir, "semantic_boundary_candidates.json")
    scene_cuts = optional_items(paths.meta_dir, "pyscenedetect_cuts.json", "cuts") or optional_items(paths.meta_dir, "pyscenedetect_cuts.json")
    visual_boundaries = optional_items(paths.meta_dir, "visual_boundary_candidates.json")
    separators = optional_items(paths.meta_dir, "separator_candidates.json")

    evidence = dedupe_evidence(
        normalize_separators(separators) + normalize_scene_cuts(scene_cuts) + normalize_visual_boundaries(visual_boundaries),
        float(args.evidence_dedupe_window),
    )
    used_ids: set[str] = set()
    items = [align_boundary(boundary, evidence, used_ids, args) for boundary in sorted(semantic_boundaries, key=lambda row: safe_float(row.get("time")))]

    counts = {
        "semantic_boundaries": len(semantic_boundaries),
        "visual_evidence_candidates": len(evidence),
        "strong_snaps": sum(1 for item in items if item.get("snap_strength") == "strong_snap"),
        "weak_snaps": sum(1 for item in items if item.get("snap_strength") == "weak_snap"),
        "evidence_only": sum(1 for item in items if item.get("alignment_type") == "evidence_only"),
        "no_visual_evidence": sum(1 for item in items if item.get("alignment_type") == "no_visual_evidence"),
        "used_visual_evidence": len(used_ids),
    }
    payload = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workspace": str(paths.workspace) if paths.workspace else "",
        "parameters": {
            "strong_snap_window": float(args.strong_snap_window),
            "weak_snap_window": float(args.weak_snap_window),
            "evidence_window": float(args.evidence_window),
            "weak_snap_min_confidence": float(args.weak_snap_min_confidence),
            "evidence_dedupe_window": float(args.evidence_dedupe_window),
            "allow_evidence_reuse": bool(args.allow_evidence_reuse),
        },
        "counts": counts,
        "used_visual_evidence_refs": sorted(used_ids),
        "items": items,
    }
    write_json(paths.meta_dir / "boundary_alignment.json", payload)
    write_text(paths.meta_dir / "boundary_alignment_summary.md", render_summary(payload))
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace": str(paths.workspace) if paths.workspace else "",
        "outputs": {
            "boundary_alignment": str(paths.meta_dir / "boundary_alignment.json"),
            "summary": str(paths.meta_dir / "boundary_alignment_summary.md"),
        },
        "counts": counts,
    }
    write_json(paths.meta_dir / "08_boundary_aligner_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Align semantic boundaries to nearby visual cuts without adding structural boundaries.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--strong-snap-window", type=float, default=0.5, help="Snap any nearby visual evidence inside this window.")
    parser.add_argument("--weak-snap-window", type=float, default=1.5, help="Snap high-confidence visual evidence inside this window.")
    parser.add_argument("--evidence-window", type=float, default=3.0, help="Record nearby visual evidence inside this window without necessarily snapping.")
    parser.add_argument("--weak-snap-min-confidence", type=float, default=0.75, help="Minimum visual confidence for weak snapping.")
    parser.add_argument("--evidence-dedupe-window", type=float, default=0.15, help="Treat evidence within this many seconds as the same visual point.")
    parser.add_argument("--max-evidence-per-boundary", type=int, default=5)
    parser.add_argument("--allow-evidence-reuse", action=argparse.BooleanOptionalAction, default=False, help="Allow multiple semantic boundaries to snap to the same visual evidence.")
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None)
    result = run_aligner(paths, args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} completed: {result['outputs']['boundary_alignment']}")


if __name__ == "__main__":
    main()
