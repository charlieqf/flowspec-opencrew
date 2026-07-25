from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "EvidenceCollector"
TOOL_VERSION = "0.1.0"


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


def optional_json(meta_dir: Path, filename: str) -> Any | None:
    path = meta_dir / filename
    if not path.exists():
        return None
    return read_json(path)


def optional_items(meta_dir: Path, filename: str, key: str = "items") -> list[dict[str, Any]]:
    payload = optional_json(meta_dir, filename)
    if not isinstance(payload, dict):
        return []
    value = payload.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def load_required_semantic_segments(meta_dir: Path) -> list[dict[str, Any]]:
    path = meta_dir / "semantic_segment_candidates.json"
    if not path.exists():
        raise DependencyError(
            "EvidenceCollector requires 03 SemanticLLMStructureBuilder output: "
            f"{path}. Run 03_semantic_llm_structure_builder.py before 10, or point --output-dir to a meta directory containing semantic_segment_candidates.json."
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


def source_ref(prefix: str, item: dict[str, Any], time_key: str = "time") -> str:
    raw_index = item.get("index") or item.get("source_cut_index") or item.get("cut_index") or item.get("segment_index")
    if raw_index is not None:
        return f"{prefix}_{raw_index}"
    return f"{prefix}_t{safe_float(item.get(time_key)):.3f}"


def load_alignment_context(meta_dir: Path) -> tuple[bool, set[str], list[dict[str, Any]], list[float]]:
    payload = optional_json(meta_dir, "boundary_alignment.json")
    if not isinstance(payload, dict):
        return False, set(), [], []
    used_refs = {str(ref) for ref in payload.get("used_visual_evidence_refs") or [] if ref}
    items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    aligned_times = []
    for item in items:
        aligned_times.append(round(safe_float(item.get("aligned_time")), 3))
        for ref in item.get("visual_evidence") or []:
            if ref:
                used_refs.add(str(ref))
    return True, used_refs, items, sorted(set(aligned_times))


def load_promoted_refs(promoted: list[dict[str, Any]]) -> set[str]:
    refs: set[str] = set()
    for item in promoted:
        for key in ["source_ref", "id"]:
            value = item.get(key)
            if value:
                refs.add(str(value))
    return refs


def load_source_refs(items: list[dict[str, Any]]) -> set[str]:
    refs: set[str] = set()
    for item in items:
        value = item.get("source_ref")
        if value:
            refs.add(str(value))
    return refs


def locate_time(time_value: float, segments: list[dict[str, Any]]) -> dict[str, Any]:
    for segment in segments:
        start = safe_float(segment.get("start"))
        end = safe_float(segment.get("end"))
        if start <= time_value <= end:
            return {
                "scope": "semantic_segment",
                "semantic_segment_index": int(segment.get("index") or 0),
                "semantic_segment_title": segment.get("title") or "",
                "segment_start": round(start, 3),
                "segment_end": round(end, 3),
            }
    ordered = sorted(segments, key=lambda item: safe_float(item.get("start")))
    for prev, nxt in zip(ordered, ordered[1:]):
        prev_end = safe_float(prev.get("end"))
        next_start = safe_float(nxt.get("start"))
        if prev_end < time_value < next_start:
            return {
                "scope": "semantic_gap",
                "after_segment_index": int(prev.get("index") or 0),
                "before_segment_index": int(nxt.get("index") or 0),
                "gap_start": round(prev_end, 3),
                "gap_end": round(next_start, 3),
            }
    return {"scope": "outside_semantic_segments"}


def common_evidence_fields(item: dict[str, Any], evidence_id: str, evidence_type: str, source: str, time_value: float, segments: list[dict[str, Any]]) -> dict[str, Any]:
    row = {
        "id": evidence_id,
        "type": evidence_type,
        "source": source,
        "time": round(time_value, 3),
        "confidence": safe_float(item.get("confidence"), 0.0),
        "reason": item.get("reason") or item.get("reject_reason") or item.get("promotion_reason") or "",
    }
    for key in ["source_ref", "reject_reason", "promotion_reason", "evidence_frame", "contact_sheet", "path", "role"]:
        if item.get(key) not in (None, "", []):
            row[key] = item[key]
    row.update(locate_time(time_value, segments))
    return row


def collect_rejected_visual(rejected: list[dict[str, Any]], segments: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    rows = []
    for index, item in enumerate(rejected[:limit], start=1):
        time_value = safe_float(item.get("time"))
        rows.append(common_evidence_fields(item, f"rejected_visual_{index:04d}", "rejected_visual_boundary", str(item.get("source") or "visual_boundary"), time_value, segments))
    return rows


def collect_alignment_evidence(alignment_items: list[dict[str, Any]], segments: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    rows = []
    for index, item in enumerate(alignment_items[:limit], start=1):
        time_value = safe_float(item.get("aligned_time"), safe_float(item.get("original_time")))
        row = common_evidence_fields(item, f"alignment_evidence_{index:04d}", "alignment_evidence", "boundary_alignment", time_value, segments)
        row.update({
            "semantic_boundary_id": item.get("semantic_boundary_id"),
            "original_time": item.get("original_time"),
            "aligned_time": item.get("aligned_time"),
            "alignment_type": item.get("alignment_type"),
            "snap_strength": item.get("snap_strength"),
            "visual_evidence": item.get("visual_evidence") or [],
        })
        rows.append(row)
    return rows


def collect_unused_scene_cuts(cuts: list[dict[str, Any]], used_refs: set[str], promoted_refs: set[str], rejected_refs: set[str], segments: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    rows = []
    for item in cuts:
        ref = source_ref("scene_cut", item)
        if ref in used_refs or ref in promoted_refs or ref in rejected_refs:
            continue
        time_value = safe_float(item.get("time"))
        row = common_evidence_fields({**item, "source_ref": ref}, f"unused_scene_cut_{len(rows) + 1:04d}", "scene_cut_evidence", "pyscenedetect_cut", time_value, segments)
        row["source_ref"] = ref
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def collect_unused_visual(items: list[dict[str, Any]], prefix: str, evidence_type: str, source: str, used_refs: set[str], promoted_refs: set[str], rejected_refs: set[str], segments: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        ref = source_ref(prefix, item)
        if ref in used_refs or ref in promoted_refs or ref in rejected_refs:
            continue
        time_value = safe_float(item.get("time"))
        row = common_evidence_fields({**item, "source_ref": ref}, f"{evidence_type}_{len(rows) + 1:04d}", evidence_type, source, time_value, segments)
        row["source_ref"] = ref
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def collect_keyframes(keyframes: list[dict[str, Any]], segments: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    rows = []
    for item in keyframes[:limit]:
        time_value = safe_float(item.get("time"))
        row = common_evidence_fields(item, f"keyframe_evidence_{len(rows) + 1:04d}", "keyframe_evidence", str(item.get("source") or "keyframe"), time_value, segments)
        rows.append(row)
    return rows


def collect_asr_silence(asr_quality: dict[str, Any] | None, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(asr_quality, dict):
        return []
    rows = []
    for item in asr_quality.get("silent_ranges") or []:
        if not isinstance(item, dict):
            continue
        start = safe_float(item.get("start"))
        end = safe_float(item.get("end"), start)
        midpoint = (start + end) / 2.0
        row = common_evidence_fields(item, f"asr_silence_{len(rows) + 1:04d}", "asr_evidence", "asr_quality", midpoint, segments)
        row.update({"start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)})
        rows.append(row)
    return rows


def dependency_status(meta_dir: Path, used_alignment: bool) -> dict[str, str]:
    files = {
        "semantic_segment_candidates": "required_present",
        "boundary_alignment": "used" if used_alignment else "missing_or_not_used",
        "promoted_visual_boundaries": "present" if (meta_dir / "promoted_visual_boundaries.json").exists() else "missing",
        "rejected_visual_boundaries": "present" if (meta_dir / "rejected_visual_boundaries.json").exists() else "missing",
        "pyscenedetect_cuts": "present" if (meta_dir / "pyscenedetect_cuts.json").exists() else "missing",
        "visual_boundary_candidates": "present" if (meta_dir / "visual_boundary_candidates.json").exists() else "missing",
        "separator_candidates": "present" if (meta_dir / "separator_candidates.json").exists() else "missing",
        "visual_keyframes": "present" if (meta_dir / "visual_keyframes.json").exists() else "missing",
        "asr_quality": "present" if (meta_dir / "asr_quality.json").exists() else "missing",
    }
    return files


def render_summary(payload: dict[str, Any]) -> str:
    counts = payload.get("counts") or {}
    lines = [
        "# Evidence Index Summary",
        "",
        f"- Tool: {TOOL_NAME} {TOOL_VERSION}",
        f"- Total Evidence Items: {counts.get('total', 0)}",
        "",
    ]
    for evidence_type, count in sorted((counts.get("by_type") or {}).items()):
        lines.append(f"- {evidence_type}: {count}")
    lines.append("")
    return "\n".join(lines)


def run_collector(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    segments = load_required_semantic_segments(paths.meta_dir)
    used_alignment, used_refs, alignment_items, _aligned_times = load_alignment_context(paths.meta_dir)
    promoted = optional_items(paths.meta_dir, "promoted_visual_boundaries.json")
    rejected = optional_items(paths.meta_dir, "rejected_visual_boundaries.json")
    promoted_refs = load_promoted_refs(promoted)
    rejected_refs = load_source_refs(rejected)

    evidence: list[dict[str, Any]] = []
    evidence.extend(collect_rejected_visual(rejected, segments, int(args.max_rejected_visual)))
    evidence.extend(collect_alignment_evidence(alignment_items, segments, int(args.max_alignment_evidence)))
    evidence.extend(collect_unused_scene_cuts(optional_items(paths.meta_dir, "pyscenedetect_cuts.json", "cuts"), used_refs, promoted_refs, rejected_refs, segments, int(args.max_unused_scene_cuts)))
    evidence.extend(collect_unused_visual(optional_items(paths.meta_dir, "visual_boundary_candidates.json"), "visual_boundary", "visual_change_evidence", "visual_boundary", used_refs, promoted_refs, rejected_refs, segments, int(args.max_visual_changes)))
    evidence.extend(collect_unused_visual(optional_items(paths.meta_dir, "separator_candidates.json"), "separator", "separator_evidence", "separator", used_refs, promoted_refs, rejected_refs, segments, int(args.max_separators)))
    evidence.extend(collect_keyframes(optional_items(paths.meta_dir, "visual_keyframes.json"), segments, int(args.max_keyframes)))
    evidence.extend(collect_asr_silence(optional_json(paths.meta_dir, "asr_quality.json"), segments))

    type_counts = Counter(str(item.get("type") or "unknown") for item in evidence)
    scope_counts = Counter(str(item.get("scope") or "unknown") for item in evidence)
    payload = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workspace": str(paths.workspace) if paths.workspace else "",
        "dependency_status": dependency_status(paths.meta_dir, used_alignment),
        "parameters": {
            "max_rejected_visual": int(args.max_rejected_visual),
            "max_alignment_evidence": int(args.max_alignment_evidence),
            "max_unused_scene_cuts": int(args.max_unused_scene_cuts),
            "max_visual_changes": int(args.max_visual_changes),
            "max_separators": int(args.max_separators),
            "max_keyframes": int(args.max_keyframes),
        },
        "counts": {
            "semantic_segments": len(segments),
            "total": len(evidence),
            "by_type": dict(sorted(type_counts.items())),
            "by_scope": dict(sorted(scope_counts.items())),
            "used_alignment_refs": len(used_refs),
            "promoted_refs": len(promoted_refs),
            "rejected_refs": len(rejected_refs),
        },
        "items": evidence,
    }
    write_json(paths.meta_dir / "evidence_index.json", payload)
    write_text(paths.meta_dir / "evidence_index_summary.md", render_summary(payload))
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace": str(paths.workspace) if paths.workspace else "",
        "outputs": {
            "evidence_index": str(paths.meta_dir / "evidence_index.json"),
            "summary": str(paths.meta_dir / "evidence_index_summary.md"),
        },
        "counts": payload["counts"],
        "dependency_status": payload["dependency_status"],
    }
    write_json(paths.meta_dir / "10_evidence_collector_result.json", result)
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
        "optional_dependencies": ["08 boundary_alignment.json", "09 promoted_visual_boundaries.json", "09 rejected_visual_boundaries.json", "04 pyscenedetect_cuts.json", "05 visual evidence outputs", "02 asr_quality.json"],
    }
    write_json(paths.meta_dir / "10_evidence_collector_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect unused and supporting visual/ASR evidence without modifying segmentation.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--max-rejected-visual", type=int, default=400)
    parser.add_argument("--max-alignment-evidence", type=int, default=200)
    parser.add_argument("--max-unused-scene-cuts", type=int, default=300)
    parser.add_argument("--max-visual-changes", type=int, default=400)
    parser.add_argument("--max-separators", type=int, default=300)
    parser.add_argument("--max-keyframes", type=int, default=500)
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None)
    try:
        result = run_collector(paths, args)
        exit_code = 0
    except DependencyError as exc:
        result = failed_result(paths, str(exc))
        exit_code = 2
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("status") == "completed":
        print(f"{TOOL_NAME} completed: {result['outputs']['evidence_index']}")
    else:
        print(f"{TOOL_NAME} failed: {result.get('message')}")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
