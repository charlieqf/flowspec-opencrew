from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "VisualEvidenceExtractor"
TOOL_VERSION = "0.1.0"


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


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_dirs(workspace: Path | None, output_dir: Path | None, keyframes_dir: Path | None) -> tuple[Path, Path]:
    if output_dir is not None:
        meta_dir = output_dir.expanduser().resolve()
    elif workspace is not None:
        meta_dir = workspace.expanduser().resolve() / "meta"
    else:
        meta_dir = Path.cwd() / "meta"
    if keyframes_dir is not None:
        frame_dir = keyframes_dir.expanduser().resolve()
    elif workspace is not None:
        frame_dir = workspace.expanduser().resolve() / "keyframes"
    else:
        frame_dir = Path.cwd() / "keyframes"
    return meta_dir, frame_dir


def relative_path(path: Path, workspace: Path | None) -> str:
    if workspace is None:
        return str(path)
    try:
        return str(path.relative_to(workspace.expanduser().resolve()))
    except ValueError:
        return str(path)


def read_video_info(video_path: Path) -> VideoInfo:
    import cv2  # type: ignore

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open video with OpenCV: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    duration = frame_count / fps if fps > 0 else 0.0
    return VideoInfo(duration, fps, frame_count, width, height)


def frame_at_time(cap: Any, fps: float, time_seconds: float) -> tuple[int, Any] | tuple[None, None]:
    import cv2  # type: ignore

    frame_index = max(0, int(round(time_seconds * fps)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = cap.read()
    if not ok:
        return None, None
    return frame_index, frame


def save_frame(video_path: Path, fps: float, time_seconds: float, output_path: Path) -> tuple[int, str] | None:
    import cv2  # type: ignore

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open video with OpenCV: {video_path}")
    frame_index, frame = frame_at_time(cap, fps, time_seconds)
    cap.release()
    if frame is None or frame_index is None:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), frame)
    return int(frame_index), str(output_path)


def frame_metrics(frame: Any) -> dict[str, float]:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    small = cv2.resize(frame, (160, 284)) if frame.shape[1] >= frame.shape[0] else cv2.resize(frame, (160, 284))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    edges = cv2.Canny(gray, 80, 160)
    hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return {
        "brightness": float(np.mean(gray)),
        "brightness_std": float(np.std(gray)),
        "color_std": float(np.mean(np.std(small, axis=(0, 1)))),
        "edge_density": float(np.mean(edges > 0)),
        "hist": hist,
        "gray": gray,
    }


def scan_frame_changes(video_path: Path, info: VideoInfo, sample_fps: float, args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open video with OpenCV: {video_path}")
    step = max(1.0 / max(sample_fps, 0.1), 1.0 / max(info.fps, 1.0))
    samples: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    t = 0.0
    while t <= info.duration_seconds:
        frame_index, frame = frame_at_time(cap, info.fps, t)
        if frame is None or frame_index is None:
            break
        metrics = frame_metrics(frame)
        frame_diff = 0.0
        hist_change = 0.0
        brightness_delta = 0.0
        edge_delta = 0.0
        if previous is not None:
            frame_diff = float(np.mean(cv2.absdiff(metrics["gray"], previous["gray"])))
            hist_change = float(1.0 - cv2.compareHist(metrics["hist"], previous["hist"], cv2.HISTCMP_CORREL))
            brightness_delta = abs(float(metrics["brightness"]) - float(previous["brightness"]))
            edge_delta = abs(float(metrics["edge_density"]) - float(previous["edge_density"]))
        public_metrics = {
            "brightness": round(float(metrics["brightness"]), 3),
            "brightness_std": round(float(metrics["brightness_std"]), 3),
            "color_std": round(float(metrics["color_std"]), 3),
            "edge_density": round(float(metrics["edge_density"]), 5),
            "frame_diff_score": round(frame_diff, 3),
            "hist_change_score": round(hist_change, 5),
            "brightness_delta": round(brightness_delta, 3),
            "edge_delta": round(edge_delta, 5),
        }
        sample = {"time": round(t, 3), "frame": int(frame_index), "metrics": public_metrics}
        samples.append(sample)
        reasons = []
        if frame_diff >= args.frame_diff_threshold:
            reasons.append("high_frame_diff")
        if hist_change >= args.hist_change_threshold:
            reasons.append("high_hist_change")
        if brightness_delta >= args.brightness_delta_threshold:
            reasons.append("brightness_delta")
        if edge_delta >= args.edge_delta_threshold:
            reasons.append("edge_delta")
        if reasons:
            confidence = min(0.95, 0.55 + 0.1 * len(reasons) + min(frame_diff / 100.0, 0.2) + min(hist_change / 2.0, 0.1))
            candidates.append({
                "index": len(candidates) + 1,
                "time": round(t, 3),
                "frame": int(frame_index),
                "type": "visual_change",
                "confidence": round(confidence, 3),
                "reason": "+".join(reasons),
                "metrics": public_metrics,
            })
        previous = metrics
        t += step
    cap.release()
    return samples, candidates


def detect_separators(samples: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for sample in samples:
        metrics = sample["metrics"]
        brightness = float(metrics["brightness"])
        color_std = float(metrics["color_std"])
        edge_density = float(metrics["edge_density"])
        candidate_type = ""
        reasons = []
        confidence = 0.0
        if brightness <= args.black_threshold and edge_density <= args.low_edge_threshold:
            candidate_type = "black_screen"
            reasons = ["low_brightness", "low_edge_density"]
            confidence = 0.92
        elif brightness >= args.bright_threshold and edge_density <= args.low_edge_threshold:
            candidate_type = "white_screen"
            reasons = ["high_brightness", "low_edge_density"]
            confidence = 0.86
        elif color_std <= args.solid_color_std_threshold and edge_density <= args.medium_edge_threshold:
            candidate_type = "solid_color_separator"
            reasons = ["low_color_std", "low_to_medium_edge_density"]
            confidence = 0.78
        elif color_std <= args.title_card_color_std_threshold and args.low_edge_threshold < edge_density <= args.title_card_edge_threshold:
            candidate_type = "title_card_candidate"
            reasons = ["stable_background", "moderate_edge_density"]
            confidence = 0.68
        elif edge_density >= args.info_insert_edge_threshold and float(metrics["brightness_std"]) >= args.info_insert_std_threshold:
            candidate_type = "info_insert_candidate"
            reasons = ["high_edge_density", "high_brightness_std"]
            confidence = 0.62
        if candidate_type:
            items.append({
                "index": len(items) + 1,
                "time": sample["time"],
                "frame": sample["frame"],
                "type": candidate_type,
                "confidence": confidence,
                "reason": "+".join(reasons),
                "metrics": metrics,
            })
    return items


def load_optional_items(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = read_json(path)
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, list) else []


def keyframe_time_points(meta_dir: Path, source: str, info: VideoInfo, args: argparse.Namespace) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    scenes = load_optional_items(meta_dir / "pyscenedetect_scenes.json", "scenes") if source in {"pyscenedetect", "both"} else []
    cuts = load_optional_items(meta_dir / "pyscenedetect_cuts.json", "cuts") if source in {"pyscenedetect", "both"} else []
    semantic = load_optional_items(meta_dir / "semantic_segment_candidates.json", "items") if source in {"semantic", "both"} else []

    if source == "pyscenedetect" and not scenes:
        source = "uniform"
    if source == "semantic" and not semantic:
        source = "uniform"
    if source == "both" and not scenes and not semantic:
        source = "uniform"

    for scene in scenes:
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or start)
        safe_end = max(start, end - min(0.1, max(0.0, end - start) / 4))
        for role, time_value in [("start", start), ("middle", (start + end) / 2), ("end_near", safe_end)]:
            points.append({"source": "pyscenedetect", "segment_index": int(scene.get("index") or 0), "role": role, "time": clamp_time(time_value, info.duration_seconds)})
    for cut in cuts:
        cut_time = float(cut.get("time") or 0.0)
        for role, delta in [("before_cut", -args.boundary_window_seconds), ("after_cut", args.boundary_window_seconds)]:
            points.append({"source": "pyscenedetect_cut", "segment_index": int(cut.get("index") or 0), "role": role, "time": clamp_time(cut_time + delta, info.duration_seconds)})
    for segment in semantic:
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        safe_end = max(start, end - min(0.1, max(0.0, end - start) / 4))
        for role, time_value in [("start", start), ("middle", (start + end) / 2), ("end_near", safe_end)]:
            points.append({"source": "semantic", "segment_index": int(segment.get("index") or 0), "role": role, "time": clamp_time(time_value, info.duration_seconds)})
    if source == "uniform":
        interval = max(args.uniform_interval_seconds, 0.5)
        count = int(math.ceil(info.duration_seconds / interval))
        for index in range(count + 1):
            t = min(info.duration_seconds, index * interval)
            points.append({"source": "uniform", "segment_index": index + 1, "role": "sample", "time": clamp_time(t, info.duration_seconds)})
    return points


def clamp_time(value: float, duration: float) -> float:
    if duration <= 0:
        return max(0.0, value)
    return round(min(max(0.0, value), max(0.0, duration - 0.001)), 3)


def write_keyframes(video_path: Path, info: VideoInfo, workspace: Path | None, keyframes_dir: Path, points: list[dict[str, Any]], visual_candidates: list[dict[str, Any]], separators: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    visual_items: list[dict[str, Any]] = []
    segment_items: dict[tuple[str, int], dict[str, Any]] = {}

    def save(role_dir: str, prefix: str, index: int, time_value: float) -> dict[str, Any] | None:
        filename = f"{prefix}_{index:04d}_t{time_value:.3f}.jpg"
        output = keyframes_dir / role_dir / filename
        saved = save_frame(video_path, info.fps, time_value, output)
        if not saved:
            return None
        frame_index, path = saved
        return {"time": round(time_value, 3), "frame": frame_index, "path": relative_path(Path(path), workspace)}

    for index, point in enumerate(points, start=1):
        source = str(point["source"])
        role = str(point["role"])
        segment_index = int(point["segment_index"])
        time_value = float(point["time"])
        role_dir = "semantic_segments" if source == "semantic" else "pyscenedetect_scenes" if source == "pyscenedetect" else "visual_candidates"
        saved = save(role_dir, f"{source}_{role}", index, time_value)
        if not saved:
            continue
        item = {"source": source, "segment_index": segment_index, "role": role, **saved}
        visual_items.append(item)
        key = (source, segment_index)
        segment_items.setdefault(key, {"segment_source": source, "segment_index": segment_index, "keyframes": []})["keyframes"].append({"role": role, **saved})

    for candidate in visual_candidates:
        saved = save("visual_candidates", "visual", int(candidate["index"]), float(candidate["time"]))
        if saved:
            candidate["evidence_frame"] = saved["path"]
    for separator in separators:
        saved = save("separators", "separator", int(separator["index"]), float(separator["time"]))
        if saved:
            separator["evidence_frame"] = saved["path"]
    return visual_items, list(segment_items.values())


def run_extractor(video_path: Path, workspace: Path | None, meta_dir: Path, keyframes_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    video_path = video_path.expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"video file does not exist: {video_path}")
    info = read_video_info(video_path)
    frame_scores, visual_candidates = scan_frame_changes(video_path, info, float(args.sample_fps), args)
    separators = detect_separators(frame_scores, args)
    points = keyframe_time_points(meta_dir, args.source, info, args)
    visual_keyframes, segment_keyframes = write_keyframes(video_path, info, workspace, keyframes_dir, points, visual_candidates, separators)

    common = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "video_path": str(video_path),
        "duration_seconds": round(info.duration_seconds, 3),
        "fps": round(info.fps, 3),
        "frame_count": info.frame_count,
        "width": info.width,
        "height": info.height,
        "source": args.source,
        "sample_fps": float(args.sample_fps),
    }
    write_json(meta_dir / "frame_change_scores.json", {**common, "items": frame_scores})
    write_json(meta_dir / "visual_boundary_candidates.json", {**common, "items": visual_candidates})
    write_json(meta_dir / "separator_candidates.json", {**common, "items": separators})
    write_json(meta_dir / "visual_keyframes.json", {**common, "items": visual_keyframes})
    write_json(meta_dir / "segment_keyframes.json", {**common, "items": segment_keyframes})
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "video_path": str(video_path),
        "outputs": {
            "frame_change_scores": str(meta_dir / "frame_change_scores.json"),
            "visual_boundary_candidates": str(meta_dir / "visual_boundary_candidates.json"),
            "separator_candidates": str(meta_dir / "separator_candidates.json"),
            "visual_keyframes": str(meta_dir / "visual_keyframes.json"),
            "segment_keyframes": str(meta_dir / "segment_keyframes.json"),
        },
        "counts": {
            "frame_scores": len(frame_scores),
            "visual_boundary_candidates": len(visual_candidates),
            "separator_candidates": len(separators),
            "visual_keyframes": len(visual_keyframes),
            "segment_keyframes": len(segment_keyframes),
        },
    }
    write_json(meta_dir / "05_visual_evidence_extractor_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract visual change evidence, separators, and keyframes independently from a video.")
    parser.add_argument("--video", required=True, help="Path to the input video file.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta and <workspace>/keyframes.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--keyframes-dir", help="Explicit keyframes output directory. Overrides --workspace/keyframes.")
    parser.add_argument("--source", choices=["uniform", "pyscenedetect", "semantic", "both"], default="pyscenedetect", help="Source used for segment keyframe extraction.")
    parser.add_argument("--sample-fps", type=float, default=2.0, help="Sampling rate for visual change scan.")
    parser.add_argument("--uniform-interval-seconds", type=float, default=2.0, help="Uniform keyframe interval when no source segments are available.")
    parser.add_argument("--boundary-window-seconds", type=float, default=0.25, help="Offset used for before/after cut keyframes.")
    parser.add_argument("--frame-diff-threshold", type=float, default=18.0)
    parser.add_argument("--hist-change-threshold", type=float, default=0.45)
    parser.add_argument("--brightness-delta-threshold", type=float, default=35.0)
    parser.add_argument("--edge-delta-threshold", type=float, default=0.08)
    parser.add_argument("--black-threshold", type=float, default=20.0)
    parser.add_argument("--bright-threshold", type=float, default=235.0)
    parser.add_argument("--solid-color-std-threshold", type=float, default=8.0)
    parser.add_argument("--title-card-color-std-threshold", type=float, default=22.0)
    parser.add_argument("--low-edge-threshold", type=float, default=0.015)
    parser.add_argument("--medium-edge-threshold", type=float, default=0.08)
    parser.add_argument("--title-card-edge-threshold", type=float, default=0.16)
    parser.add_argument("--info-insert-edge-threshold", type=float, default=0.18)
    parser.add_argument("--info-insert-std-threshold", type=float, default=45.0)
    parser.add_argument("--print-json", action="store_true", help="Print result JSON to stdout.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else None
    meta_dir, keyframes_dir = resolve_dirs(workspace, Path(args.output_dir) if args.output_dir else None, Path(args.keyframes_dir) if args.keyframes_dir else None)
    result = run_extractor(Path(args.video), workspace, meta_dir, keyframes_dir, args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} completed: {result['outputs']['segment_keyframes']}")


if __name__ == "__main__":
    main()
