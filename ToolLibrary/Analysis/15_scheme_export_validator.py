from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_binaries import find_ffmpeg, media_env


TOOL_NAME = "SchemeExportValidator"
TOOL_VERSION = "0.2.0"
SCHEME_CHOICES = ["detail", "balanced", "summary"]


class DependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Paths:
    workspace: Path | None
    meta_dir: Path
    schemes_dir: Path
    reports_dir: Path


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def workspace_relative(path: Path, workspace: Path | None) -> str:
    if workspace:
        try:
            return str(path.expanduser().absolute().relative_to(workspace.expanduser().absolute()))
        except ValueError:
            pass
    return str(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_paths(workspace: Path | None, output_dir: Path | None, schemes_dir: Path | None, reports_dir: Path | None) -> Paths:
    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    meta_dir = output_dir.expanduser().resolve() if output_dir else (resolved_workspace / "meta" if resolved_workspace else Path.cwd() / "meta")
    resolved_schemes = schemes_dir.expanduser().resolve() if schemes_dir else (resolved_workspace / "schemes" if resolved_workspace else Path.cwd() / "schemes")
    resolved_reports = reports_dir.expanduser().resolve() if reports_dir else (resolved_workspace / "reports" if resolved_workspace else Path.cwd() / "reports")
    return Paths(workspace=resolved_workspace, meta_dir=meta_dir, schemes_dir=resolved_schemes, reports_dir=resolved_reports)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_scheme(meta_dir: Path, scheme: str) -> list[dict[str, Any]]:
    path = meta_dir / f"scheme_{scheme}_segments.json"
    if not path.exists():
        raise DependencyError(f"15 requires 13 FineTimelineBuilder output: {path}")
    payload = read_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise DependencyError(f"Invalid 13 output: {path} must contain non-empty items list")
    return sorted([item for item in items if isinstance(item, dict)], key=lambda item: safe_float(item.get("start")))


def load_asr_segments(meta_dir: Path) -> list[dict[str, Any]]:
    sentence_path = meta_dir / "asr_sentence_timeline.json"
    if sentence_path.exists():
        payload = read_json(sentence_path)
        items = payload.get("items") if isinstance(payload, dict) else None
        if isinstance(items, list) and items:
            return [item for item in items if isinstance(item, dict)]
    path = meta_dir / "asr_segments.json"
    if not path.exists():
        raise DependencyError(f"15 requires ASR output for subtitle cutting: {path}")
    payload = read_json(path)
    items = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise DependencyError(f"Invalid ASR output: {path} must contain segments list")
    return [item for item in items if isinstance(item, dict)]


def load_segment_description(meta_dir: Path, scheme: str, segment_index: int) -> dict[str, Any]:
    path = meta_dir / "segment_descriptions" / f"scheme_{scheme}" / f"segment_{segment_index:03d}.json"
    if not path.exists():
        raise DependencyError(f"15 requires 14 segment retake description output: {path}")
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("retake_fields"), dict):
        raise DependencyError(f"Invalid 14 description output: {path} must contain retake_fields object")
    return payload


def load_video_path(meta_dir: Path, explicit_video: str | None) -> Path:
    if explicit_video:
        path = Path(explicit_video).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"video file does not exist: {path}")
        return path
    candidates = [
        (meta_dir / "video_metadata.json", "path"),
        (meta_dir / "pyscenedetect_cuts.json", "video_path"),
        (meta_dir / "pyscenedetect_scenes.json", "video_path"),
    ]
    for path, key in candidates:
        if not path.exists():
            continue
        value = read_json(path).get(key)
        if value:
            video_path = Path(str(value)).expanduser().resolve()
            if video_path.exists():
                return video_path
    raise DependencyError("15 requires a source video path from --video, video_metadata.json, or pyscenedetect_cuts.json")


def load_video_duration(meta_dir: Path, schemes: dict[str, list[dict[str, Any]]]) -> float:
    path = meta_dir / "video_metadata.json"
    if path.exists():
        duration = safe_float(read_json(path).get("duration_seconds"), 0.0)
        if duration > 0:
            return duration
    path = meta_dir / "pyscenedetect_cuts.json"
    if path.exists():
        duration = safe_float(read_json(path).get("duration_seconds"), 0.0)
        if duration > 0:
            return duration
    ends = [safe_float(item.get("end")) for items in schemes.values() for item in items]
    duration = max(ends or [0.0])
    if duration <= 0:
        raise DependencyError("15 requires video duration from metadata or scheme segment ends")
    return duration


def overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def subtitle_items_for_segment(segment: dict[str, Any], asr_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"))
    rows = []
    for item in asr_segments:
        item_start = safe_float(item.get("start"))
        item_end = safe_float(item.get("end"), item_start)
        if overlap_seconds(start, end, item_start, item_end) <= 0:
            continue
        clipped_start = max(item_start, start)
        clipped_end = min(item_end, end)
        rows.append({
            "index": len(rows) + 1,
            "start": round(clipped_start - start, 3),
            "end": round(clipped_end - start, 3),
            "absolute_start": round(clipped_start, 3),
            "absolute_end": round(clipped_end, 3),
            "text": str(item.get("text") or "").strip(),
            "source_asr_index": item.get("index"),
        })
    semantic_rows = semantic_subtitle_items_for_segment(segment, rows)
    if semantic_rows:
        return semantic_rows
    if not rows:
        fallback_text = str(segment.get("dialogue_text") or "").strip()
        if not fallback_text:
            title = str(segment.get("title") or "").strip()
            role = str(segment.get("semantic_role") or "").strip()
            fallback_text = f"[画面文字] {title or role}".strip()
        if fallback_text and fallback_text != "[画面文字]":
            rows.append({
                "index": 1,
                "start": 0.0,
                "end": round(max(0.001, end - start), 3),
                "absolute_start": round(start, 3),
                "absolute_end": round(end, 3),
                "text": fallback_text,
                "source_asr_index": None,
                "source": "segment_fallback",
            })
    return rows


def normalize_subtitle_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", re.sub(r"\s+", "", str(text or ""))).lower()


def split_dialogue_text(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*|[；;]\s*", normalized) if part.strip()]
    return parts or [normalized]


def semantic_subtitle_items_for_segment(segment: dict[str, Any], asr_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dialogue_text = str(segment.get("dialogue_text") or "").strip()
    if not dialogue_text:
        return []
    asr_text = "".join(str(item.get("text") or "") for item in asr_rows).strip()
    if normalize_subtitle_text(dialogue_text) == normalize_subtitle_text(asr_text):
        return []
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"))
    duration = max(0.001, end - start)
    parts = split_dialogue_text(dialogue_text)
    if not parts:
        return []
    rows = []
    slice_duration = duration / len(parts)
    for index, part in enumerate(parts, start=1):
        local_start = round((index - 1) * slice_duration, 3)
        local_end = round(duration if index == len(parts) else index * slice_duration, 3)
        rows.append({
            "index": index,
            "start": local_start,
            "end": max(local_end, local_start + 0.001),
            "absolute_start": round(start + local_start, 3),
            "absolute_end": round(start + max(local_end, local_start + 0.001), 3),
            "text": part,
            "source": "semantic_dialogue_text",
        })
    return rows


def srt_timestamp(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_srt(items: list[dict[str, Any]]) -> str:
    blocks = []
    for index, item in enumerate(items, start=1):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        blocks.append(f"{index}\n{srt_timestamp(safe_float(item.get('start')))} --> {srt_timestamp(safe_float(item.get('end')))}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def export_clip(ffmpeg: str, video_path: Path, output_path: Path, start: float, duration: float, mode: str, overwrite: bool) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        return {"status": "skipped_existing", "path": str(output_path), "command": []}
    if mode == "copy":
        command = [ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{duration:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero", str(output_path)]
    else:
        command = [
            ffmpeg,
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    result = subprocess.run(command, text=True, capture_output=True, env=media_env())
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "ffmpeg clip export failed").strip()
        raise RuntimeError(f"failed to export clip {output_path}: {message}")
    return {"status": "exported", "path": str(output_path), "command": command}


def clean_scheme_output_dir(out_dir: Path) -> None:
    if not out_dir.exists():
        return
    for pattern in ("segment_*.mp4", "segment_*.srt", "segment_*.json"):
        for path in out_dir.glob(pattern):
            if path.is_file():
                path.unlink()


def validate_timeline(items: list[dict[str, Any]], duration: float, tolerance: float) -> dict[str, Any]:
    issues = []
    rows = sorted(items, key=lambda item: safe_float(item.get("start")))
    if not rows:
        issues.append({"type": "empty_timeline"})
        return {"valid": False, "issues": issues, "segment_count": 0}
    first_start = safe_float(rows[0].get("start"))
    last_end = safe_float(rows[-1].get("end"))
    sentence_timed = all("asr_sentence_timeline" in str(item.get("boundary_source") or "") for item in rows)
    if abs(first_start) > tolerance and not sentence_timed:
        issues.append({"type": "start_not_zero", "actual": first_start, "expected": 0.0})
    if abs(last_end - duration) > tolerance and not sentence_timed:
        issues.append({"type": "end_not_duration", "actual": last_end, "expected": duration})
    for left, right in zip(rows, rows[1:]):
        left_end = safe_float(left.get("end"))
        right_start = safe_float(right.get("start"))
        delta = round(right_start - left_end, 6)
        if delta > tolerance and not sentence_timed:
            issues.append({"type": "gap", "left_index": left.get("index"), "right_index": right.get("index"), "seconds": delta})
        elif delta < -tolerance:
            issues.append({"type": "overlap", "left_index": left.get("index"), "right_index": right.get("index"), "seconds": abs(delta)})
    bad_duration = [item for item in rows if safe_float(item.get("duration"), safe_float(item.get("end")) - safe_float(item.get("start"))) <= 0]
    for item in bad_duration:
        issues.append({"type": "non_positive_duration", "index": item.get("index"), "duration": item.get("duration")})
    return {
        "valid": not issues,
        "issues": issues,
        "segment_count": len(rows),
        "sentence_timed": sentence_timed,
        "start": first_start,
        "end": last_end,
        "duration_seconds": duration,
    }


def scheme_output_name(position: int) -> str:
    return f"scheme_{position}"


def export_scheme(paths: Paths, scheme: str, output_name: str, segments: list[dict[str, Any]], asr_segments: list[dict[str, Any]], video_path: Path, args: argparse.Namespace, ffmpeg: str | None) -> dict[str, Any]:
    out_dir = paths.schemes_dir / output_name
    clean_scheme_output_dir(out_dir)
    manifest_items = []
    virtual_source = paths.workspace / "source_video.mp4" if paths.workspace else None
    source_video_path = virtual_source if virtual_source and virtual_source.exists() else video_path
    for segment in segments:
        idx = int(segment.get("index") or len(manifest_items) + 1)
        start = safe_float(segment.get("start"))
        end = safe_float(segment.get("end"))
        duration = max(0.0, end - start)
        subtitles = subtitle_items_for_segment(segment, asr_segments)
        srt_path = out_dir / f"segment_{idx:03d}.srt"
        mp4_path = out_dir / f"segment_{idx:03d}.mp4"
        retake_path = out_dir / f"segment_{idx:03d}.json"
        write_text(srt_path, render_srt(subtitles))
        write_json(retake_path, load_segment_description(paths.meta_dir, scheme, idx))
        if str(args.clip_mode) == "virtual":
            clip_result = {"status": "virtual", "path": workspace_relative(source_video_path, paths.workspace), "command": []}
        else:
            if not ffmpeg:
                raise DependencyError("15 requires ffmpeg when --clip-mode is encode or copy")
            clip_result = export_clip(ffmpeg, video_path, mp4_path, start, duration, str(args.clip_mode), bool(args.overwrite))
        manifest_items.append({
            "segment_index": idx,
            "source_scheme": scheme,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
            "title": segment.get("title") or "",
            "srt_path": workspace_relative(srt_path, paths.workspace),
            "clip_path": workspace_relative(mp4_path if str(args.clip_mode) != "virtual" else source_video_path, paths.workspace),
            "source_video_path": workspace_relative(source_video_path, paths.workspace),
            "retake_description_path": workspace_relative(retake_path, paths.workspace),
            "clip_status": clip_result["status"],
            "subtitle_items": len(subtitles),
        })
        print(f"[15] scheme={output_name} segment={idx:03d}/{len(segments)} clip={clip_result['status']}", flush=True)
    manifest = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "scheme": scheme,
        "output_name": output_name,
        "video_path": str(video_path),
        "source_video_path": workspace_relative(source_video_path, paths.workspace),
        "clip_mode": str(args.clip_mode),
        "items": manifest_items,
    }
    write_json(out_dir / "manifest.json", manifest)
    return manifest


def parse_schemes(value: str) -> list[str]:
    schemes = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [item for item in schemes if item not in SCHEME_CHOICES]
    if invalid:
        raise ValueError(f"invalid schemes: {', '.join(invalid)}")
    return schemes or ["balanced"]


def run_builder(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    selected_schemes = parse_schemes(str(args.schemes))
    schemes = {scheme: load_scheme(paths.meta_dir, scheme) for scheme in selected_schemes}
    asr_segments = load_asr_segments(paths.meta_dir)
    video_path = load_video_path(paths.meta_dir, args.video)
    duration = load_video_duration(paths.meta_dir, schemes)
    ffmpeg = None if str(args.clip_mode) == "virtual" else find_ffmpeg()

    coverage = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "video_path": str(video_path),
        "duration_seconds": duration,
        "tolerance_seconds": float(args.coverage_tolerance),
        "selected_schemes": selected_schemes,
        "partial_export": selected_schemes != SCHEME_CHOICES,
        "schemes": {},
    }
    manifests = []
    for position, scheme in enumerate(selected_schemes, start=1):
        output_name = scheme_output_name(position)
        coverage["schemes"][output_name] = {"source_scheme": scheme, **validate_timeline(schemes[scheme], duration, float(args.coverage_tolerance))}
        manifests.append(export_scheme(paths, scheme, output_name, schemes[scheme], asr_segments, video_path, args, ffmpeg))

    coverage["valid"] = all(item.get("valid") for item in coverage["schemes"].values())
    write_json(paths.reports_dir / "timeline_coverage_check.json", coverage)

    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed" if coverage["valid"] else "completed_with_coverage_issues",
        "workspace": str(paths.workspace) if paths.workspace else "",
        "video_path": str(video_path),
        "selected_schemes": selected_schemes,
        "partial_export": selected_schemes != SCHEME_CHOICES,
        "scheme_mapping": {item["output_name"]: item["scheme"] for item in manifests},
        "outputs": {
            "schemes_dir": str(paths.schemes_dir),
            "coverage_report": str(paths.reports_dir / "timeline_coverage_check.json"),
            "result": str(paths.meta_dir / "15_scheme_export_validator_result.json"),
        },
        "counts": {
            "schemes": len(manifests),
            "segments": sum(len(item.get("items") or []) for item in manifests),
            "clips": sum(1 for manifest in manifests for item in manifest.get("items", []) if item.get("clip_status") in {"exported", "skipped_existing", "virtual"}),
            "srts": sum(len(item.get("items") or []) for item in manifests),
            "retake_descriptions": sum(len(item.get("items") or []) for item in manifests),
        },
        "coverage_valid": coverage["valid"],
        "manifests": [str(paths.schemes_dir / item["output_name"] / "manifest.json") for item in manifests],
    }
    write_json(paths.meta_dir / "15_scheme_export_validator_result.json", result)
    return result


def failed_result(paths: Paths, message: str) -> dict[str, Any]:
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "failed",
        "workspace": str(paths.workspace) if paths.workspace else "",
        "error_code": "missing_dependency",
        "message": message,
        "required_dependencies": ["13 scheme outputs", "14 segment_descriptions", "02 asr_segments.json", "source video", "ffmpeg"],
    }
    write_json(paths.meta_dir / "15_scheme_export_validator_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export scheme subtitles and virtual clips, then validate timeline coverage.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/schemes and <workspace>/reports.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--schemes-dir", help="Explicit schemes output directory. Overrides --workspace/schemes.")
    parser.add_argument("--reports-dir", help="Explicit reports output directory. Overrides --workspace/reports.")
    parser.add_argument("--video", help="Optional source video path. If omitted, read from metadata.")
    parser.add_argument("--schemes", default="detail,balanced,summary", help="Comma-separated schemes to export. Default: detail,balanced,summary. Choices: detail,balanced,summary.")
    parser.add_argument("--clip-mode", choices=["virtual", "encode", "copy"], default="virtual", help="virtual writes start/end metadata only; encode/copy exports physical mp4 clips.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing mp4 clips. By default existing clips are reused.")
    parser.add_argument("--coverage-tolerance", type=float, default=0.05)
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None, Path(args.schemes_dir) if args.schemes_dir else None, Path(args.reports_dir) if args.reports_dir else None)
    try:
        result = run_builder(paths, args)
        exit_code = 0 if result.get("coverage_valid") else 1
    except (DependencyError, RuntimeError, ValueError, FileNotFoundError) as exc:
        result = failed_result(paths, str(exc))
        exit_code = 2
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("status", "").startswith("completed"):
        print(f"{TOOL_NAME} completed: {result['outputs']['coverage_report']}")
    else:
        print(f"{TOOL_NAME} failed: {result.get('message')}")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
