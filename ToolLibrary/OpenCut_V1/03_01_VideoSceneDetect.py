from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLLIB_ROOT = REPO_ROOT / "ToolLibrary"
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))

try:
    from OpenCut_V1.scene_runtime import (
        BlockedError,
        base_result,
        finish_error,
        load_inputs,
        now_iso,
        read_json,
        relpath,
        remove_path,
        resolve_workspace,
        source_fingerprint,
        write_json,
    )
except Exception:  # pragma: no cover - direct script fallback
    from scene_runtime import (  # type: ignore
        BlockedError,
        base_result,
        finish_error,
        load_inputs,
        now_iso,
        read_json,
        relpath,
        remove_path,
        resolve_workspace,
        source_fingerprint,
        write_json,
    )


TOOL_NAME = "03_01_VideoSceneDetect"
TOOL_VERSION = "0.2.0"
TOOL_DIR = "S2_03_01_VideoSceneDetect"
OUTPUT_CUTS_REL = f"{TOOL_DIR}/Output/scene_detect_cuts.json"
OUTPUT_SCENES_REL = f"{TOOL_DIR}/Output/scene_detect_scenes.json"
STATE_REL = f"{TOOL_DIR}/State.json"
REPORT_REL = f"{TOOL_DIR}/Report/Result.json"
SESSION_CUTS_REL = "SessionOutput/visual/scene_detect_cuts.json"
SESSION_SCENES_REL = "SessionOutput/visual/scene_detect_scenes.json"
DETECTORS = ("content", "adaptive")
CONTENT_THRESHOLD = 27.0
ADAPTIVE_THRESHOLD = 3.0
MIN_SCENE_SECONDS = 0.5
MERGE_WINDOW_SECONDS = 0.35
MAX_ANALYSIS_SEGMENT_SECONDS = 15.0


@dataclass(frozen=True)
class Args:
    workspace: str
    force: bool
    resume: bool
    print_json: bool


def run_detector(video_path: Path, detector_name: str, min_scene_frames: int) -> list[dict[str, Any]]:
    try:
        from scenedetect import SceneManager, open_video  # type: ignore
        from scenedetect.detectors import AdaptiveDetector, ContentDetector  # type: ignore
    except Exception as exc:
        raise BlockedError("pyscenedetect_missing", "PySceneDetect is required for OpenCut visual analysis.") from exc

    detector = (
        ContentDetector(threshold=CONTENT_THRESHOLD, min_scene_len=min_scene_frames)
        if detector_name == "content"
        else AdaptiveDetector(adaptive_threshold=ADAPTIVE_THRESHOLD, min_scene_len=min_scene_frames)
    )
    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(detector)
    manager.detect_scenes(video=video, show_progress=False)
    scenes = manager.get_scene_list()
    return [
        {
            "time": round(float(scene[0].get_seconds()), 3),
            "frame": int(scene[0].get_frames()),
            "detector": detector_name,
        }
        for scene in scenes[1:]
    ]


def merge_cuts(raw_cuts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for cut in sorted(raw_cuts, key=lambda item: (float(item["time"]), str(item["detector"]))):
        if not groups or float(cut["time"]) - max(float(item["time"]) for item in groups[-1]) > MERGE_WINDOW_SECONDS:
            groups.append([cut])
        else:
            groups[-1].append(cut)
    merged: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        detectors = sorted({str(item["detector"]) for item in group})
        merged.append({
            "index": index,
            "time": round(sum(float(item["time"]) for item in group) / len(group), 3),
            "frame": int(round(sum(int(item["frame"]) for item in group) / len(group))),
            "source_detectors": detectors,
            "confidence": 0.82 if len(detectors) == 2 else 0.7,
        })
    return merged


def _split_long_scenes(
    scenes: list[dict[str, Any]],
    *,
    fps: float,
    max_duration: float = MAX_ANALYSIS_SEGMENT_SECONDS,
) -> list[dict[str, Any]]:
    """Bound representative-frame coverage without claiming extra camera cuts."""

    if max_duration <= 0:
        raise ValueError("max_analysis_segment_seconds_invalid")
    bounded: list[dict[str, Any]] = []
    for scene in scenes:
        start = float(scene["start"])
        end = float(scene["end"])
        duration = end - start
        window_count = max(1, int(math.ceil(duration / max_duration)))
        for window_index in range(window_count):
            window_start = start + (duration * window_index / window_count)
            window_end = (
                end
                if window_index == window_count - 1
                else start + (duration * (window_index + 1) / window_count)
            )
            item = dict(scene)
            item.update(
                {
                    "start": round(window_start, 3),
                    "end": round(window_end, 3),
                    "duration": round(window_end - window_start, 3),
                    "start_frame": int(round(window_start * fps)),
                    "end_frame": max(
                        int(round(window_start * fps)),
                        int(round(window_end * fps)) - 1,
                    ),
                    "segment_kind": (
                        "long_scene_window"
                        if window_count > 1
                        else "detected_scene"
                    ),
                    "source_scene_index": int(scene.get("index") or 0),
                    "analysis_window_index": window_index + 1,
                    "analysis_window_count": window_count,
                }
            )
            bounded.append(item)
    return bounded


def build_scenes(cuts: list[dict[str, Any]], *, duration: float, fps: float, frame_count: int) -> list[dict[str, Any]]:
    valid = [cut for cut in cuts if 0 < float(cut["time"]) < duration]
    scenes: list[dict[str, Any]] = []
    start = 0.0
    start_frame = 0
    for cut in valid:
        end = float(cut["time"])
        if end - start < MIN_SCENE_SECONDS:
            continue
        scenes.append({
            "index": len(scenes) + 1,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "start_frame": start_frame,
            "end_frame": int(cut["frame"]),
            "source_detectors": list(cut.get("source_detectors") or []),
            "confidence": float(cut.get("confidence") or 0.7),
        })
        start = end
        start_frame = int(cut["frame"])
    if duration > start:
        scenes.append({
            "index": len(scenes) + 1,
            "start": round(start, 3),
            "end": round(duration, 3),
            "duration": round(duration - start, 3),
            "start_frame": start_frame,
            "end_frame": max(start_frame, frame_count - 1),
            "source_detectors": [],
            "confidence": 1.0 if not valid else 0.7,
        })
    if not scenes:
        scenes.append({
            "index": 1,
            "start": 0.0,
            "end": round(duration, 3),
            "duration": round(duration, 3),
            "start_frame": 0,
            "end_frame": max(0, frame_count - 1),
            "source_detectors": [],
            "confidence": 1.0,
        })
    if len(scenes) > 1 and float(scenes[-1]["duration"]) < MIN_SCENE_SECONDS:
        tail = scenes.pop()
        scenes[-1]["end"] = tail["end"]
        scenes[-1]["end_frame"] = tail["end_frame"]
        scenes[-1]["duration"] = round(float(tail["end"]) - float(scenes[-1]["start"]), 3)
    scenes = _split_long_scenes(scenes, fps=fps)
    for index, scene in enumerate(scenes, start=1):
        scene["index"] = index
        scene["scene_id"] = f"scene_{index:04d}"
        if index > 1:
            scene["start"] = scenes[index - 2]["end"]
            scene["duration"] = round(float(scene["end"]) - float(scene["start"]), 3)
            scene["start_frame"] = int(round(float(scene["start"]) * fps))
    return scenes


def reusable(workspace: Path, fingerprint: str) -> dict[str, Any] | None:
    path = workspace / SESSION_SCENES_REL
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except Exception:
        return None
    return payload if payload.get("source_fingerprint") == fingerprint else None


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(TOOL_NAME, TOOL_VERSION, workspace, force=args.force, resume=args.resume)
    try:
        if args.force:
            for rel in (TOOL_DIR, SESSION_CUTS_REL, SESSION_SCENES_REL):
                remove_path(workspace / rel)
                result["cleanup_actions"].append({"path": rel, "action": "removed_for_force_rerun"})
        variables, source, metadata = load_inputs(workspace)
        fingerprint = source_fingerprint(source)
        existing = reusable(workspace, fingerprint) if args.resume and not args.force else None
        if existing is not None:
            result["outputs"] = {"cuts": SESSION_CUTS_REL, "scenes": SESSION_SCENES_REL}
            result["warnings"].append({"code": "reused_completed_output", "message": "Existing Scene Detect output was reused."})
        else:
            duration = float(metadata.get("duration_seconds") or 0.0)
            fps = float(metadata.get("fps") or 0.0)
            frame_count = int(metadata.get("frame_count") or 0)
            if duration <= 0 or fps <= 0 or frame_count <= 0:
                raise BlockedError("video_metadata_incomplete", "Scene Detect requires duration, FPS and frame count.")
            min_scene_frames = max(1, int(round(MIN_SCENE_SECONDS * fps)))
            raw_cuts = [cut for detector in DETECTORS for cut in run_detector(source, detector, min_scene_frames)]
            cuts = merge_cuts(raw_cuts)
            scenes = build_scenes(cuts, duration=duration, fps=fps, frame_count=frame_count)
            common = {
                "schema_version": "opencut_v1_scene_detect_0.1",
                "source_video_path": relpath(source, workspace),
                "source_fingerprint": fingerprint,
                "duration_seconds": round(duration, 3),
                "fps": round(fps, 3),
                "frame_count": frame_count,
                "detectors": list(DETECTORS),
                "parameters": {
                    "content_threshold": CONTENT_THRESHOLD,
                    "adaptive_threshold": ADAPTIVE_THRESHOLD,
                    "min_scene_seconds": MIN_SCENE_SECONDS,
                    "merge_window_seconds": MERGE_WINDOW_SECONDS,
                    "max_analysis_segment_seconds": MAX_ANALYSIS_SEGMENT_SECONDS,
                },
                "created_at": now_iso(),
            }
            cuts_payload = {**common, "raw_cut_count": len(raw_cuts), "cuts": cuts}
            scenes_payload = {**common, "scenes": scenes}
            for rel, payload in ((OUTPUT_CUTS_REL, cuts_payload), (SESSION_CUTS_REL, cuts_payload), (OUTPUT_SCENES_REL, scenes_payload), (SESSION_SCENES_REL, scenes_payload)):
                write_json(workspace / rel, payload)
            result["inputs"] = {"variables": variables, "source_video": relpath(source, workspace)}
            result["outputs"] = {"cuts": SESSION_CUTS_REL, "scenes": SESSION_SCENES_REL}
            result["created_files"] = [OUTPUT_CUTS_REL, OUTPUT_SCENES_REL, SESSION_CUTS_REL, SESSION_SCENES_REL, STATE_REL, REPORT_REL]
        write_json(workspace / STATE_REL, {"tool": TOOL_NAME, "status": "completed", "outputs": result["outputs"], "updated_at": now_iso()})
    except Exception as exc:
        finish_error(result, exc)
    write_json(workspace / REPORT_REL, result)
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Detect OpenCut V1 visual scenes with PySceneDetect.")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(str(ns.workspace or ""), bool(ns.force), bool(ns.resume), bool(ns.print_json))


def main(argv: list[str] | None = None) -> int:
    cli = argv if argv is not None else sys.argv[1:]
    if "--tool-session-root" in cli:
        from ToolLibrary.OpenCut_V1.framework_bridge import maybe_run_framework_bridge
        bridged = maybe_run_framework_bridge(cli, script_path=Path(__file__), tool_name=TOOL_NAME)
        if bridged is not None:
            return bridged
    args = parse_args(cli)
    result = run(args)
    code = 0 if result["status"] == "completed" else 2 if result["status"] == "blocked" else 1
    if args.print_json or code:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
