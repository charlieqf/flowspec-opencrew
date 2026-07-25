from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "SceneSRTCalibrator"
TOOL_VERSION = "0.1.0"


@dataclass(frozen=True)
class Paths:
    workspace: Path | None
    meta_dir: Path
    transcripts_dir: Path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def optional_items(meta_dir: Path, filename: str, key: str = "items") -> list[dict[str, Any]]:
    path = meta_dir / filename
    if not path.exists():
        return []
    payload = read_json(path)
    value = payload.get(key) if isinstance(payload, dict) else None
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def ranges_overlap(left_start: float, left_end: float, right_start: float, right_end: float) -> bool:
    return max(left_start, right_start) <= min(max(left_start, left_end), max(right_start, right_end))


def overlap_items(start: float, end: float, items: list[dict[str, Any]], window: float = 0.0) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        item_start = safe_float(item.get("start"), safe_float(item.get("time")))
        item_end = safe_float(item.get("end"), item_start)
        if ranges_overlap(start - window, end + window, item_start, item_end):
            rows.append(item)
    return rows


def srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        secs += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_srt(items: list[dict[str, Any]]) -> str:
    blocks = []
    for idx, item in enumerate(items, start=1):
        text = re.sub(r"\s+", " ", str(item.get("text") or item.get("preferred_text") or "")).strip()
        if not text:
            continue
        blocks.append(f"{idx}\n{srt_time(safe_float(item.get('start')))} --> {srt_time(safe_float(item.get('end'), safe_float(item.get('start'))))}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def asr_reliability(asr_quality: dict[str, Any]) -> float:
    level = str(asr_quality.get("quality_level") or "unknown")
    score = {"good": 0.86, "usable": 0.62, "weak": 0.35, "failed": 0.1}.get(level, 0.5)
    if bool(asr_quality.get("timestamp_coverage_suspect")):
        score -= 0.18
    return max(0.0, min(1.0, score))


def fallback_scenes(meta_dir: Path, alignment: list[dict[str, Any]], asr_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    duration = 0.0
    metadata_path = meta_dir / "video_metadata.json"
    if metadata_path.exists():
        duration = safe_float(read_json(metadata_path).get("duration_seconds"))
    if duration <= 0:
        duration = max([safe_float(item.get("end")) for item in alignment + asr_items] + [0.0])
    return [{"index": 1, "start": 0.0, "end": duration}]


def preferred_scene_items(scene: dict[str, Any], alignment: list[dict[str, Any]], asr_items: list[dict[str, Any]], visual_text: list[dict[str, Any]], asr_score: float, args: argparse.Namespace) -> tuple[list[dict[str, Any]], str, list[str]]:
    start = safe_float(scene.get("start"))
    end = safe_float(scene.get("end"), start)
    aligned = overlap_items(start, end, alignment, window=float(args.scene_overlap_window))
    warnings: list[str] = []
    if aligned:
        policy = "ocr_boundary_primary" if any(str(item.get("preferred_source")) == "subtitle_ocr" for item in aligned) and asr_score < 0.72 else "asr_ocr_bidirectional"
        items = []
        for item in aligned:
            item_start = safe_float(item.get("start"))
            item_end = safe_float(item.get("end"), item_start)
            items.append({
                "start": round(max(start, item_start), 3),
                "end": round(min(end, max(item_start, item_end)), 3),
                "text": str(item.get("preferred_text") or item.get("text") or ""),
                "preferred_source": item.get("preferred_source"),
                "alignment_policy": item.get("alignment_policy"),
                "source_asr_segment_ids": item.get("source_asr_segment_ids") or [],
                "source_ocr_item_ids": item.get("source_ocr_item_ids") or [],
            })
            if item.get("needs_review"):
                warnings.append("alignment_item_needs_review")
        return items, policy, sorted(set(warnings))

    asr_rows = overlap_items(start, end, asr_items, window=float(args.scene_overlap_window))
    items = [{"start": max(start, safe_float(item.get("start"))), "end": min(end, safe_float(item.get("end"), safe_float(item.get("start")))), "text": item.get("text"), "preferred_source": "asr", "alignment_policy": "fallback_asr", "source_asr_segment_ids": [item.get("index")]} for item in asr_rows if str(item.get("text") or "").strip()]
    if not items:
        warnings.append("scene_has_no_srt_candidate")
    return items, "fallback_asr", warnings


def build_scene_calibration(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    asr = read_json(paths.meta_dir / "asr_segments.json")
    asr_quality = read_json(paths.meta_dir / "asr_quality.json") if (paths.meta_dir / "asr_quality.json").exists() else {}
    asr_items = [item for item in (asr.get("segments") or []) if isinstance(item, dict)]
    scenes = optional_items(paths.meta_dir, "pyscenedetect_scenes.json", "scenes")
    alignment = optional_items(paths.meta_dir, "subtitle_alignment_timeline.json") or optional_items(paths.meta_dir, "visual_subtitle_timeline_calibrated.json")
    visual_text = optional_items(paths.meta_dir, "visual_text_timeline.json")
    if not scenes:
        scenes = fallback_scenes(paths.meta_dir, alignment, asr_items)
    score = asr_reliability(asr_quality)
    scene_srt_dir = paths.transcripts_dir / "scene_srt"
    rows: list[dict[str, Any]] = []
    srt_segments: list[dict[str, Any]] = []

    for scene in scenes:
        scene_index = int(scene.get("index") or len(rows) + 1)
        scene_start = safe_float(scene.get("start"))
        scene_end = safe_float(scene.get("end"), scene_start)
        items, policy, warnings = preferred_scene_items(scene, alignment, asr_items, visual_text, score, args)
        context = [str(item.get("text") or item.get("ocr_text") or "") for item in overlap_items(scene_start, scene_end, visual_text, window=0.25)]
        calibrated_start = min([safe_float(item.get("start")) for item in items] + [scene_start])
        calibrated_end = max([safe_float(item.get("end"), safe_float(item.get("start"))) for item in items] + [scene_end])
        calibrated_start = max(scene_start, calibrated_start)
        calibrated_end = min(scene_end, calibrated_end)
        srt_path = scene_srt_dir / f"scene_{scene_index:03d}.srt"
        write_text(srt_path, render_srt(items))
        dialogue_text = "".join(str(item.get("text") or "") for item in items).strip()
        row = {
            "scene_index": scene_index,
            "scene_start": round(scene_start, 3),
            "scene_end": round(scene_end, 3),
            "calibrated_srt_start": round(calibrated_start, 3),
            "calibrated_srt_end": round(calibrated_end, 3),
            "dialogue_text": dialogue_text,
            "preferred_text": dialogue_text,
            "preferred_source": "subtitle_alignment" if alignment else "asr",
            "alignment_policy": policy,
            "confidence": round(score if policy == "fallback_asr" else max(score, 0.7), 4),
            "source_asr_segment_ids": sorted({seg_id for item in items for seg_id in (item.get("source_asr_segment_ids") or []) if seg_id}),
            "source_ocr_item_ids": sorted({str(ocr_id) for item in items for ocr_id in (item.get("source_ocr_item_ids") or []) if ocr_id}),
            "visual_text_context": [value for value in context if value],
            "srt_path": str(srt_path),
            "subtitle_items": items,
            "warnings": sorted(set(warnings)),
        }
        rows.append(row)
        srt_segments.append({"index": scene_index, "start": round(scene_start, 3), "end": round(scene_end, 3), "duration": round(scene_end - scene_start, 3), "dialogue_text": dialogue_text, "title": f"Scene {scene_index}", "semantic_role": "scene_srt_calibrated", "formula_slot": "", "confidence": row["confidence"], "source_scene_index": scene_index, "srt_path": str(srt_path), "visual_text_context": row["visual_text_context"], "alignment_policy": policy})

    common = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "workspace": str(paths.workspace) if paths.workspace else ""}
    write_json(paths.meta_dir / "scene_srt_calibration.json", {**common, "items": rows})
    write_json(paths.meta_dir / "scene_srt_segments.json", {**common, "items": srt_segments})
    result = {**common, "status": "completed", "outputs": {"scene_srt_calibration": str(paths.meta_dir / "scene_srt_calibration.json"), "scene_srt_segments": str(paths.meta_dir / "scene_srt_segments.json"), "scene_srt_dir": str(scene_srt_dir)}, "counts": {"scenes": len(scenes), "scene_srt_segments": len(srt_segments), "needs_review": sum(1 for item in rows if item.get("warnings"))}}
    write_json(paths.meta_dir / "13_01_scene_srt_calibrator_result.json", result)
    return result


def resolve_paths(workspace: Path | None, output_dir: Path | None, transcripts_dir: Path | None) -> Paths:
    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    meta_dir = output_dir.expanduser().resolve() if output_dir else (resolved_workspace / "meta" if resolved_workspace else Path.cwd() / "meta")
    resolved_transcripts = transcripts_dir.expanduser().resolve() if transcripts_dir else (resolved_workspace / "transcripts" if resolved_workspace else Path.cwd() / "transcripts")
    return Paths(resolved_workspace, meta_dir, resolved_transcripts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate per-scene SRT with ASR/OCR subtitle alignment and SceneDetect scenes.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta and <workspace>/transcripts.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--transcripts-dir", help="Explicit transcripts output directory. Overrides --workspace/transcripts.")
    parser.add_argument("--scene-overlap-window", type=float, default=0.15)
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args()
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None, Path(args.transcripts_dir) if args.transcripts_dir else None)
    result = build_scene_calibration(paths, args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} completed: {result['outputs']['scene_srt_segments']}")


if __name__ == "__main__":
    main()
