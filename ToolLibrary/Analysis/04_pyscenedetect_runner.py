from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "PySceneDetectRunner"
TOOL_VERSION = "0.1.0"
DEFAULT_DETECTORS = ["content", "adaptive"]
SUPPORTED_DETECTORS = {"content", "adaptive", "threshold"}


@dataclass(frozen=True)
class VideoInfo:
    duration_seconds: float
    fps: float
    frame_count: int
    width: int
    height: int


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def resolve_output_dir(workspace: Path | None, output_dir: Path | None) -> Path:
    if output_dir is not None:
        return output_dir.expanduser().resolve()
    if workspace is None:
        return Path.cwd() / "meta"
    return workspace.expanduser().resolve() / "meta"


def read_video_info(video_path: Path) -> VideoInfo:
    try:
        import cv2  # type: ignore
    except Exception as exc:
        raise RuntimeError("opencv-python is required to read video metadata for scene timelines") from exc

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"failed to open video with OpenCV: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    capture.release()
    duration = frame_count / fps if fps > 0 else 0.0
    return VideoInfo(duration_seconds=duration, fps=fps, frame_count=frame_count, width=width, height=height)


def min_scene_len_frames(fps: float, min_scene_seconds: float) -> int:
    return max(1, int(round(max(0.0, min_scene_seconds) * max(fps, 1.0))))


def build_detector(name: str, args: argparse.Namespace, min_scene_len: int) -> Any:
    from scenedetect.detectors import AdaptiveDetector, ContentDetector, ThresholdDetector  # type: ignore

    if name == "content":
        return ContentDetector(threshold=float(args.content_threshold), min_scene_len=min_scene_len)
    if name == "adaptive":
        return AdaptiveDetector(adaptive_threshold=float(args.adaptive_threshold), min_scene_len=min_scene_len)
    if name == "threshold":
        return ThresholdDetector(threshold=float(args.threshold_threshold), min_scene_len=min_scene_len)
    raise ValueError(f"unsupported detector: {name}")


def run_detector(video_path: Path, detector_name: str, args: argparse.Namespace, min_scene_len: int) -> list[dict[str, Any]]:
    from scenedetect import SceneManager, open_video  # type: ignore

    video = open_video(str(video_path))
    scene_manager = SceneManager()
    scene_manager.add_detector(build_detector(detector_name, args, min_scene_len))
    scene_manager.detect_scenes(video=video, show_progress=False)
    cuts = []
    scene_list = scene_manager.get_scene_list()
    cut_list = [scene[0] for scene in scene_list[1:]]
    for cut in cut_list:
        cuts.append({
            "time": round(float(cut.get_seconds()), 3),
            "frame": int(cut.get_frames()),
            "detector": detector_name,
            "reason": f"pyscenedetect_{detector_name}",
        })
    return cuts


def merge_cuts(raw_cuts: list[dict[str, Any]], merge_window_seconds: float) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for cut in sorted(raw_cuts, key=lambda item: (float(item["time"]), str(item["detector"]))):
        previous_time = max(merged[-1]["time_values"]) if merged else None
        if previous_time is None or float(cut["time"]) - float(previous_time) > merge_window_seconds:
            merged.append({
                "time_values": [float(cut["time"])],
                "frame_values": [int(cut["frame"])],
                "source_detectors": [str(cut["detector"])],
                "reasons": [str(cut["reason"])],
            })
            continue
        current = merged[-1]
        current["time_values"].append(float(cut["time"]))
        current["frame_values"].append(int(cut["frame"]))
        if str(cut["detector"]) not in current["source_detectors"]:
            current["source_detectors"].append(str(cut["detector"]))
        if str(cut["reason"]) not in current["reasons"]:
            current["reasons"].append(str(cut["reason"]))

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(merged, start=1):
        detectors = sorted(item["source_detectors"])
        if len(detectors) >= 3:
            confidence = 0.9
        elif len(detectors) == 2:
            confidence = 0.82
        elif detectors == ["threshold"]:
            confidence = 0.65
        else:
            confidence = 0.7
        normalized.append({
            "index": index,
            "time": round(sum(item["time_values"]) / len(item["time_values"]), 3),
            "frame": int(round(sum(item["frame_values"]) / len(item["frame_values"]))),
            "source_detectors": detectors,
            "confidence": confidence,
            "reason": "+".join(sorted(item["reasons"])),
        })
    return normalized


def build_scenes(cuts: list[dict[str, Any]], video_info: VideoInfo, min_scene_seconds: float) -> list[dict[str, Any]]:
    scenes: list[dict[str, Any]] = []
    current_start = 0.0
    current_frame = 0
    scene_sources: list[str] = []
    filtered_cuts = [cut for cut in cuts if 0.0 < float(cut["time"]) < video_info.duration_seconds]
    for cut in filtered_cuts:
        cut_time = float(cut["time"])
        cut_frame = int(cut["frame"])
        if cut_time - current_start < min_scene_seconds:
            scene_sources.extend([item for item in cut.get("source_detectors", []) if item not in scene_sources])
            continue
        scenes.append({
            "index": len(scenes) + 1,
            "start": round(current_start, 3),
            "end": round(cut_time, 3),
            "duration": round(cut_time - current_start, 3),
            "start_frame": current_frame,
            "end_frame": cut_frame,
            "source_detectors": list(cut.get("source_detectors", [])),
        })
        current_start = cut_time
        current_frame = cut_frame
        scene_sources = list(cut.get("source_detectors", []))
    if video_info.duration_seconds > current_start:
        scenes.append({
            "index": len(scenes) + 1,
            "start": round(current_start, 3),
            "end": round(video_info.duration_seconds, 3),
            "duration": round(video_info.duration_seconds - current_start, 3),
            "start_frame": current_frame,
            "end_frame": max(0, video_info.frame_count - 1),
            "source_detectors": scene_sources,
        })
    if not scenes:
        scenes.append({
            "index": 1,
            "start": 0.0,
            "end": round(video_info.duration_seconds, 3),
            "duration": round(video_info.duration_seconds, 3),
            "start_frame": 0,
            "end_frame": max(0, video_info.frame_count - 1),
            "source_detectors": [],
        })
    return scenes


def render_summary(cuts_payload: dict[str, Any], scenes_payload: dict[str, Any]) -> str:
    lines = [
        "# PySceneDetect Summary",
        "",
        f"- Tool: {TOOL_NAME} {TOOL_VERSION}",
        f"- Video: {cuts_payload.get('video_path')}",
        f"- Profile: {cuts_payload.get('profile')}",
        f"- Pass: {cuts_payload.get('pass_name')}",
        f"- Detectors: {', '.join(cuts_payload.get('detectors') or [])}",
        f"- Cuts: {len(cuts_payload.get('cuts') or [])}",
        f"- Scenes: {len(scenes_payload.get('scenes') or [])}",
        "",
    ]
    for scene in scenes_payload.get("scenes") or []:
        lines.append(f"- Scene {scene['index']}: {scene['start']}s - {scene['end']}s ({scene['duration']}s)")
    lines.append("")
    return "\n".join(lines)


def run_pyscenedetect(video_path: Path, output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    video_path = video_path.expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"video file does not exist: {video_path}")
    if not video_path.is_file():
        raise ValueError(f"video path is not a file: {video_path}")

    detectors = args.detectors or DEFAULT_DETECTORS
    unsupported = [name for name in detectors if name not in SUPPORTED_DETECTORS]
    if unsupported:
        raise ValueError(f"unsupported detectors: {', '.join(unsupported)}")

    video_info = read_video_info(video_path)
    min_len = min_scene_len_frames(video_info.fps, float(args.min_scene_seconds))
    raw_cuts: list[dict[str, Any]] = []
    raw_passes: list[dict[str, Any]] = []
    for detector in detectors:
        detector_cuts = run_detector(video_path, detector, args, min_len)
        raw_cuts.extend(detector_cuts)
        raw_passes.append({"pass_name": args.pass_name, "profile": args.profile, "detector": detector, "cuts": detector_cuts})

    merged_cuts = merge_cuts(raw_cuts, float(args.merge_window_seconds))
    scenes = build_scenes(merged_cuts, video_info, float(args.min_scene_seconds))
    common = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "video_path": str(video_path),
        "profile": args.profile,
        "pass_name": args.pass_name,
        "detectors": detectors,
        "duration_seconds": round(video_info.duration_seconds, 3),
        "fps": round(video_info.fps, 3),
        "frame_count": video_info.frame_count,
        "width": video_info.width,
        "height": video_info.height,
    }
    cuts_payload = {
        **common,
        "merge_window_seconds": float(args.merge_window_seconds),
        "min_scene_seconds": float(args.min_scene_seconds),
        "raw_cut_count": len(raw_cuts),
        "cuts": merged_cuts,
    }
    scenes_payload = {
        **common,
        "scenes": scenes,
    }
    passes_payload = {
        **common,
        "passes": raw_passes,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "pyscenedetect_cuts.json", cuts_payload)
    write_json(output_dir / "pyscenedetect_scenes.json", scenes_payload)
    write_json(output_dir / "pyscenedetect_passes.json", passes_payload)
    write_text(output_dir / "pyscenedetect_summary.md", render_summary(cuts_payload, scenes_payload))
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "video_path": str(video_path),
        "profile": args.profile,
        "pass_name": args.pass_name,
        "detectors": detectors,
        "outputs": {
            "cuts": str(output_dir / "pyscenedetect_cuts.json"),
            "scenes": str(output_dir / "pyscenedetect_scenes.json"),
            "passes": str(output_dir / "pyscenedetect_passes.json"),
            "summary": str(output_dir / "pyscenedetect_summary.md"),
        },
        "counts": {
            "raw_cuts": len(raw_cuts),
            "merged_cuts": len(merged_cuts),
            "scenes": len(scenes),
        },
    }
    write_json(output_dir / "04_pyscenedetect_runner_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PySceneDetect as an independent visual scene/cut detector.")
    parser.add_argument("--video", required=True, help="Path to the input video file.")
    parser.add_argument("--workspace", help="Task workspace path. If --output-dir is omitted, writes to <workspace>/meta.")
    parser.add_argument("--output-dir", help="Explicit output directory. Overrides --workspace and keeps the tool directory-agnostic.")
    parser.add_argument("--detectors", nargs="+", choices=sorted(SUPPORTED_DETECTORS), default=DEFAULT_DETECTORS, help="Detectors to run once for this pass.")
    parser.add_argument("--profile", default="balanced", choices=["balanced", "high_recall", "high_precision", "visual_only"], help="Reserved profile marker for future multi-pass optimization.")
    parser.add_argument("--pass-name", default="single", help="Reserved pass marker for future reruns/multi-pass recall.")
    parser.add_argument("--content-threshold", type=float, default=27.0, help="ContentDetector threshold.")
    parser.add_argument("--adaptive-threshold", type=float, default=3.0, help="AdaptiveDetector adaptive_threshold.")
    parser.add_argument("--threshold-threshold", type=float, default=12.0, help="ThresholdDetector threshold.")
    parser.add_argument("--merge-window-seconds", type=float, default=0.35, help="Merge cuts from different detectors within this time window.")
    parser.add_argument("--min-scene-seconds", type=float, default=0.5, help="Minimum scene duration in seconds.")
    parser.add_argument("--print-json", action="store_true", help="Print result JSON to stdout.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    workspace = Path(args.workspace) if args.workspace else None
    output_dir = resolve_output_dir(workspace, Path(args.output_dir) if args.output_dir else None)
    result = run_pyscenedetect(Path(args.video), output_dir, args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} completed: {result['outputs']['scenes']}")


if __name__ == "__main__":
    main()
