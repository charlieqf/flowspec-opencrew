from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - handled at runtime
    cv2 = None  # type: ignore


TOOL_NAME = "03_VideoPySceneDetect"
TOOL_VERSION = "0.1.0"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
DEFAULT_SOURCE_VIDEO_REL = f"{CONTEXT_DIR_NAME}/Video_Source.mp4"
DEFAULT_METADATA_REL = f"{CONTEXT_DIR_NAME}/Video_Metadata.json"
TOOL_DIR_NAME = "S3_03_VideoPySceneDetect"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_METADATA_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_1_Video_Metadata.json"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
OUTPUT_SCENE_CUTS_REL = f"{TOOL_DIR_NAME}/Output/scene_cuts.json"
OUTPUT_SCENE_SEGMENTS_REL = f"{TOOL_DIR_NAME}/Output/scene_segments.json"
OUTPUT_VISUAL_KEYFRAMES_REL = f"{TOOL_DIR_NAME}/Output/visual_keyframes.json"
OUTPUT_SEGMENT_KEYFRAMES_REL = f"{TOOL_DIR_NAME}/Output/segment_keyframes.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SESSION_VISUAL_DIR_REL = "SessionOutput/visual"
SESSION_SCENE_CUTS_REL = f"{SESSION_VISUAL_DIR_REL}/scene_cuts.json"
SESSION_SCENE_SEGMENTS_REL = f"{SESSION_VISUAL_DIR_REL}/scene_segments.json"
SESSION_VISUAL_KEYFRAMES_REL = f"{SESSION_VISUAL_DIR_REL}/visual_keyframes.json"
SESSION_SEGMENT_KEYFRAMES_REL = f"{SESSION_VISUAL_DIR_REL}/segment_keyframes.json"
SESSION_KEYFRAMES_DIR_REL = f"{SESSION_VISUAL_DIR_REL}/keyframes"
DEFAULT_DETECTORS = ["content", "adaptive"]
SUPPORTED_DETECTORS = {"content", "adaptive", "threshold"}
SECRET_PATTERNS = (
    "postgresql://",
    "postgresql+psycopg://",
    "password",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth header",
    "cookie",
)


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Args:
    workspace: str
    detectors: list[str]
    content_threshold: float
    adaptive_threshold: float
    threshold_threshold: float
    merge_window_seconds: float
    min_scene_seconds: float
    boundary_window_seconds: float
    force: bool
    resume: bool
    print_json: bool


@dataclass(frozen=True)
class VideoInfo:
    duration_seconds: float
    fps: float
    frame_count: int
    width: int
    height: int


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def relpath(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser() if raw_workspace else Path.cwd()
    try:
        return workspace.resolve()
    except Exception:
        return workspace.absolute()


def validate_workspace(workspace: Path) -> None:
    if not workspace.exists():
        raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise BlockedError("workspace_not_directory", f"Workspace is not a directory: {workspace}")


def load_variables(workspace: Path) -> dict[str, Any]:
    path = workspace / VARIABLES_REL
    if not path.exists():
        raise BlockedError(
            "variables_missing",
            f"Required SessionContext file is missing: {VARIABLES_REL}. Run 00_PrepareSessionVariables.py first.",
        )
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    return payload


def load_video_metadata(workspace: Path, variables: dict[str, Any]) -> dict[str, Any]:
    raw = str(variables.get("video_metadata_path") or DEFAULT_METADATA_REL).strip()
    path = Path(raw)
    if path.is_absolute():
        raise BlockedError("video_metadata_path_not_relative", "video_metadata_path must be workspace-relative.")
    path = workspace / path
    if not path.exists():
        raise BlockedError(
            "video_metadata_missing",
            f"Required video metadata is missing: {raw}. Run 01_VideoProbeMetadata.py first.",
        )
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("video_metadata_invalid", f"{raw} must contain a JSON object.")
    if float(payload.get("duration_seconds") or 0.0) <= 0:
        raise BlockedError("video_duration_missing", "Video metadata must contain duration_seconds.")
    if float(payload.get("fps") or 0.0) <= 0:
        raise BlockedError("video_fps_missing", "Video metadata must contain fps.")
    if int(payload.get("frame_count") or 0) <= 0:
        raise BlockedError("video_frame_count_missing", "Video metadata must contain frame_count.")
    return payload


def resolve_source_video(workspace: Path, variables: dict[str, Any]) -> Path:
    raw = str(variables.get("source_video_path") or DEFAULT_SOURCE_VIDEO_REL).strip()
    path = Path(raw)
    if path.is_absolute():
        raise BlockedError("source_video_path_not_relative", "source_video_path must be workspace-relative.")
    path = workspace / path
    if not path.exists():
        raise BlockedError(
            "source_video_missing",
            f"Source video is missing: {raw}. Run 00_PrepareSessionVariables.py first.",
        )
    if not path.is_file():
        raise BlockedError("source_video_not_file", f"Source video is not a file: {raw}")
    return path


def ensure_tool_dirs(workspace: Path) -> None:
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        f"{TOOL_DIR_NAME}/Report",
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def source_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "fingerprint": hashlib.sha256(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")).hexdigest(),
    }


def config_signature(args: Args) -> str:
    payload = {
        "detectors": args.detectors,
        "content_threshold": float(args.content_threshold),
        "adaptive_threshold": float(args.adaptive_threshold),
        "threshold_threshold": float(args.threshold_threshold),
        "merge_window_seconds": float(args.merge_window_seconds),
        "min_scene_seconds": float(args.min_scene_seconds),
        "boundary_window_seconds": float(args.boundary_window_seconds),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def metadata_video_info(metadata: dict[str, Any]) -> VideoInfo:
    return VideoInfo(
        duration_seconds=float(metadata.get("duration_seconds") or 0.0),
        fps=float(metadata.get("fps") or 0.0),
        frame_count=int(metadata.get("frame_count") or 0),
        width=int(metadata.get("width") or 0),
        height=int(metadata.get("height") or 0),
    )


def min_scene_len_frames(fps: float, min_scene_seconds: float) -> int:
    return max(1, int(round(max(0.0, min_scene_seconds) * max(fps, 1.0))))


def build_detector(name: str, args: Args, min_scene_len: int) -> Any:
    try:
        from scenedetect.detectors import AdaptiveDetector, ContentDetector, ThresholdDetector  # type: ignore
    except Exception as exc:
        raise BlockedError("pyscenedetect_missing", "PySceneDetect is not available in this runtime.") from exc

    if name == "content":
        return ContentDetector(threshold=float(args.content_threshold), min_scene_len=min_scene_len)
    if name == "adaptive":
        return AdaptiveDetector(adaptive_threshold=float(args.adaptive_threshold), min_scene_len=min_scene_len)
    if name == "threshold":
        return ThresholdDetector(threshold=float(args.threshold_threshold), min_scene_len=min_scene_len)
    raise BlockedError("unsupported_detector", f"Unsupported detector: {name}")


def run_detector(video_path: Path, detector_name: str, args: Args, min_scene_len: int) -> list[dict[str, Any]]:
    try:
        from scenedetect import SceneManager, open_video  # type: ignore
    except Exception as exc:
        raise BlockedError("pyscenedetect_missing", "PySceneDetect is not available in this runtime.") from exc

    video = open_video(str(video_path))
    scene_manager = SceneManager()
    scene_manager.add_detector(build_detector(detector_name, args, min_scene_len))
    scene_manager.detect_scenes(video=video, show_progress=False)
    scene_list = scene_manager.get_scene_list()
    cuts: list[dict[str, Any]] = []
    for cut in [scene[0] for scene in scene_list[1:]]:
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
        cut_sources = [str(item) for item in cut.get("source_detectors", [])]
        if cut_time - current_start < min_scene_seconds:
            for source in cut_sources:
                if source not in scene_sources:
                    scene_sources.append(source)
            continue
        scenes.append({
            "index": len(scenes) + 1,
            "start": round(current_start, 3),
            "end": round(cut_time, 3),
            "duration": round(cut_time - current_start, 3),
            "start_frame": current_frame,
            "end_frame": cut_frame,
            "source_detectors": cut_sources or scene_sources,
        })
        current_start = cut_time
        current_frame = cut_frame
        scene_sources = cut_sources
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


def clamp_time(value: float, duration: float) -> float:
    if duration <= 0:
        return max(0.0, value)
    return round(min(max(0.0, value), max(0.0, duration - 0.001)), 3)


def keyframe_time_points(scenes: list[dict[str, Any]], cuts: list[dict[str, Any]], info: VideoInfo, args: Args) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for scene in scenes:
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or start)
        safe_end = max(start, end - min(0.1, max(0.0, end - start) / 4))
        for role, time_value in [("start", start), ("middle", (start + end) / 2), ("end_near", safe_end)]:
            points.append({
                "source": "pyscenedetect",
                "segment_index": int(scene.get("index") or 0),
                "role": role,
                "time": clamp_time(time_value, info.duration_seconds),
            })
    for cut in cuts:
        cut_time = float(cut.get("time") or 0.0)
        for role, delta in [("before_cut", -args.boundary_window_seconds), ("after_cut", args.boundary_window_seconds)]:
            points.append({
                "source": "pyscenedetect_cut",
                "segment_index": int(cut.get("index") or 0),
                "role": role,
                "time": clamp_time(cut_time + delta, info.duration_seconds),
            })
    return points


def frame_at_time(cap: Any, fps: float, time_seconds: float) -> tuple[int, Any] | tuple[None, None]:
    if cv2 is None:
        raise BlockedError("opencv_missing", "opencv-python is required to extract keyframes.")
    frame_index = max(0, int(round(time_seconds * fps)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = cap.read()
    if not ok:
        return None, None
    return frame_index, frame


def save_frame(video_path: Path, fps: float, time_seconds: float, output_path: Path) -> tuple[int, str] | None:
    if cv2 is None:
        raise BlockedError("opencv_missing", "opencv-python is required to extract keyframes.")
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise BlockedError("opencv_video_open_failed", f"Failed to open video with OpenCV: {video_path}")
    frame_index, frame = frame_at_time(cap, fps, time_seconds)
    cap.release()
    if frame is None or frame_index is None:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), frame):
        raise RuntimeError(f"Failed to write keyframe: {output_path}")
    return int(frame_index), str(output_path)


def write_keyframes(video_path: Path, info: VideoInfo, workspace: Path, keyframes_dir: Path, points: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    visual_items: list[dict[str, Any]] = []
    segment_items: dict[tuple[str, int], dict[str, Any]] = {}
    warnings: list[dict[str, str]] = []

    def save(prefix: str, index: int, time_value: float) -> dict[str, Any] | None:
        filename = f"{prefix}_{index:04d}_t{time_value:.3f}.jpg"
        output = keyframes_dir / filename
        saved = save_frame(video_path, info.fps, time_value, output)
        if not saved:
            return None
        frame_index, path = saved
        return {"time": round(time_value, 3), "frame": frame_index, "path": relpath(Path(path), workspace)}

    for index, point in enumerate(points, start=1):
        source = str(point["source"])
        role = str(point["role"])
        segment_index = int(point["segment_index"])
        time_value = float(point["time"])
        saved = save(f"{source}_{role}", index, time_value)
        if not saved:
            warnings.append({"code": "keyframe_extract_failed", "message": f"Could not extract keyframe at {time_value:.3f}s."})
            continue
        item = {"index": len(visual_items) + 1, "source": source, "segment_index": segment_index, "role": role, **saved}
        visual_items.append(item)
        key = (source, segment_index)
        segment_items.setdefault(key, {"segment_source": source, "segment_index": segment_index, "keyframes": []})["keyframes"].append({"role": role, **saved})
    return visual_items, list(segment_items.values()), warnings


def run_pyscenedetect(video_path: Path, metadata: dict[str, Any], args: Args) -> tuple[dict[str, Any], dict[str, Any]]:
    unsupported = [name for name in args.detectors if name not in SUPPORTED_DETECTORS]
    if unsupported:
        raise BlockedError("unsupported_detector", f"Unsupported detectors: {', '.join(unsupported)}")
    info = metadata_video_info(metadata)
    min_len = min_scene_len_frames(info.fps, float(args.min_scene_seconds))
    raw_cuts: list[dict[str, Any]] = []
    raw_passes: list[dict[str, Any]] = []
    for detector in args.detectors:
        detector_cuts = run_detector(video_path, detector, args, min_len)
        raw_cuts.extend(detector_cuts)
        raw_passes.append({"detector": detector, "cuts": detector_cuts})

    cuts = merge_cuts(raw_cuts, float(args.merge_window_seconds))
    scenes = build_scenes(cuts, info, float(args.min_scene_seconds))
    common = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "source_video_path": relpath(video_path, video_path.parents[1]) if video_path.name == "Video_Source.mp4" else str(video_path),
        "detectors": args.detectors,
        "duration_seconds": round(info.duration_seconds, 3),
        "fps": round(info.fps, 3),
        "frame_count": info.frame_count,
        "width": info.width,
        "height": info.height,
        "created_at": now_iso(),
    }
    cuts_payload = {
        "schema_version": "analysis_v1_scene_cuts_0.1",
        **common,
        "merge_window_seconds": float(args.merge_window_seconds),
        "min_scene_seconds": float(args.min_scene_seconds),
        "raw_cut_count": len(raw_cuts),
        "raw_passes": raw_passes,
        "cuts": cuts,
    }
    scenes_payload = {
        "schema_version": "analysis_v1_scene_segments_0.1",
        **common,
        "scenes": scenes,
    }
    return cuts_payload, scenes_payload


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": False,
        "reads_session_context": [VARIABLES_REL, DEFAULT_METADATA_REL, DEFAULT_SOURCE_VIDEO_REL],
        "writes_session_context": [],
        "writes_session_output": [
            SESSION_SCENE_CUTS_REL,
            SESSION_SCENE_SEGMENTS_REL,
            SESSION_VISUAL_KEYFRAMES_REL,
            SESSION_SEGMENT_KEYFRAMES_REL,
            SESSION_KEYFRAMES_DIR_REL,
        ],
        "created_files": [],
        "prepared_directories": [],
        "cleanup_actions": [],
        "inputs": {},
        "outputs": {},
        "counts": {},
        "warnings": [],
        "blocked_reasons": [],
        "resume": bool(args.resume),
        "force": bool(args.force),
        "updated_at": now_iso(),
    }


def add_block(result: dict[str, Any], code: str, message: str) -> None:
    result["status"] = "blocked"
    result.setdefault("blocked_reasons", []).append({"code": code, "message": message})


def scan_for_sensitive_output(payload: dict[str, Any]) -> list[dict[str, str]]:
    text = json.dumps(payload, ensure_ascii=False).lower()
    warnings: list[dict[str, str]] = []
    for pattern in SECRET_PATTERNS:
        if pattern in text:
            warnings.append({"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"})
    return warnings


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    cleanup_actions = result.setdefault("cleanup_actions", [])
    for rel in (
        TOOL_DIR_NAME,
        SESSION_SCENE_CUTS_REL,
        SESSION_SCENE_SEGMENTS_REL,
        SESSION_VISUAL_KEYFRAMES_REL,
        SESSION_SEGMENT_KEYFRAMES_REL,
        SESSION_KEYFRAMES_DIR_REL,
    ):
        path = workspace / rel
        if path.exists():
            remove_path(path)
            cleanup_actions.append({"path": rel, "action": "removed_for_force_rerun"})


def prepare_inputs(workspace: Path, variables: dict[str, Any], metadata: dict[str, Any], source_video: Path, source_info: dict[str, Any], signature: str, result: dict[str, Any]) -> dict[str, Any]:
    ensure_tool_dirs(workspace)
    for rel in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", f"{TOOL_DIR_NAME}/Report"):
        result.setdefault("prepared_directories", []).append(rel)
    write_json(workspace / WORKING_VARIABLES_REL, variables)
    write_json(workspace / WORKING_METADATA_REL, metadata)
    state = {
        "tool": TOOL_NAME,
        "status": "ready",
        "phase": "prepare",
        "source": source_info,
        "config_signature": signature,
        "inputs": {
            "variables": WORKING_VARIABLES_REL,
            "video_metadata": WORKING_METADATA_REL,
            "source_video": relpath(source_video, workspace),
        },
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["inputs"] = state["inputs"]
    return state


def payload_keyframe_paths(payload: dict[str, Any]) -> list[str]:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    paths = []
    for item in items:
        if isinstance(item, dict) and item.get("path"):
            paths.append(str(item["path"]))
    return paths


def load_reusable_outputs(workspace: Path, source_info: dict[str, Any], signature: str, force: bool) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    if force:
        return None
    paths = [
        workspace / OUTPUT_SCENE_CUTS_REL,
        workspace / OUTPUT_SCENE_SEGMENTS_REL,
        workspace / OUTPUT_VISUAL_KEYFRAMES_REL,
        workspace / OUTPUT_SEGMENT_KEYFRAMES_REL,
        workspace / WORKING_STATE_REL,
    ]
    if not all(path.exists() for path in paths):
        return None
    try:
        state = read_json(workspace / WORKING_STATE_REL)
        cuts = read_json(workspace / OUTPUT_SCENE_CUTS_REL)
        scenes = read_json(workspace / OUTPUT_SCENE_SEGMENTS_REL)
        visual_keyframes = read_json(workspace / OUTPUT_VISUAL_KEYFRAMES_REL)
        segment_keyframes = read_json(workspace / OUTPUT_SEGMENT_KEYFRAMES_REL)
    except Exception:
        return None
    if state.get("status") != "completed":
        return None
    if (state.get("source") or {}).get("fingerprint") != source_info.get("fingerprint"):
        return None
    if state.get("config_signature") != signature:
        return None
    for rel in payload_keyframe_paths(visual_keyframes):
        if not (workspace / rel).exists():
            return None
    return cuts, scenes, visual_keyframes, segment_keyframes


def finalize_outputs(workspace: Path, cuts: dict[str, Any], scenes: dict[str, Any], visual_keyframes: dict[str, Any], segment_keyframes: dict[str, Any], state: dict[str, Any], result: dict[str, Any], reused: bool) -> None:
    write_json(workspace / OUTPUT_SCENE_CUTS_REL, cuts)
    write_json(workspace / OUTPUT_SCENE_SEGMENTS_REL, scenes)
    write_json(workspace / OUTPUT_VISUAL_KEYFRAMES_REL, visual_keyframes)
    write_json(workspace / OUTPUT_SEGMENT_KEYFRAMES_REL, segment_keyframes)
    write_json(workspace / SESSION_SCENE_CUTS_REL, cuts)
    write_json(workspace / SESSION_SCENE_SEGMENTS_REL, scenes)
    write_json(workspace / SESSION_VISUAL_KEYFRAMES_REL, visual_keyframes)
    write_json(workspace / SESSION_SEGMENT_KEYFRAMES_REL, segment_keyframes)
    state = {
        **state,
        "status": "completed",
        "phase": "finalize",
        "outputs": {
            "scene_cuts": OUTPUT_SCENE_CUTS_REL,
            "scene_segments": OUTPUT_SCENE_SEGMENTS_REL,
            "visual_keyframes": OUTPUT_VISUAL_KEYFRAMES_REL,
            "segment_keyframes": OUTPUT_SEGMENT_KEYFRAMES_REL,
            "session_scene_cuts": SESSION_SCENE_CUTS_REL,
            "session_scene_segments": SESSION_SCENE_SEGMENTS_REL,
            "session_visual_keyframes": SESSION_VISUAL_KEYFRAMES_REL,
            "session_segment_keyframes": SESSION_SEGMENT_KEYFRAMES_REL,
            "session_keyframes_dir": SESSION_KEYFRAMES_DIR_REL,
        },
        "reused_completed_output": reused,
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["status"] = "completed"
    result["outputs"] = state["outputs"]
    result["counts"] = {
        "scene_cuts": len(cuts.get("cuts") or []),
        "scene_segments": len(scenes.get("scenes") or []),
        "visual_keyframes": len(visual_keyframes.get("items") or []),
        "segment_keyframes": len(segment_keyframes.get("items") or []),
    }
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_METADATA_REL,
        WORKING_STATE_REL,
        OUTPUT_SCENE_CUTS_REL,
        OUTPUT_SCENE_SEGMENTS_REL,
        OUTPUT_VISUAL_KEYFRAMES_REL,
        OUTPUT_SEGMENT_KEYFRAMES_REL,
        SESSION_SCENE_CUTS_REL,
        SESSION_SCENE_SEGMENTS_REL,
        SESSION_VISUAL_KEYFRAMES_REL,
        SESSION_SEGMENT_KEYFRAMES_REL,
        REPORT_RESULT_REL,
    ]
    if reused:
        result["warnings"].append({"code": "reused_completed_output", "message": "Existing PySceneDetect output was reused because the input fingerprint and detector signature matched."})


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    try:
        validate_workspace(workspace)
        if args.force:
            force_reset(workspace, result)
        variables = load_variables(workspace)
        metadata = load_video_metadata(workspace, variables)
        source_video = resolve_source_video(workspace, variables)
        source_info = source_fingerprint(source_video)
        signature = config_signature(args)
        reusable = load_reusable_outputs(workspace, source_info, signature, args.force or not args.resume)
        state = prepare_inputs(workspace, variables, metadata, source_video, source_info, signature, result)
        if reusable is not None:
            cuts, scenes, visual_keyframes, segment_keyframes = reusable
            finalize_outputs(workspace, cuts, scenes, visual_keyframes, segment_keyframes, state, result, reused=True)
        else:
            state = {**state, "phase": "pyscenedetect", "updated_at": now_iso()}
            write_json(workspace / WORKING_STATE_REL, state)
            cuts, scenes = run_pyscenedetect(source_video, metadata, args)
            info = metadata_video_info(metadata)
            state = {**state, "phase": "keyframe_extract", "updated_at": now_iso()}
            write_json(workspace / WORKING_STATE_REL, state)
            keyframes_dir = workspace / SESSION_KEYFRAMES_DIR_REL
            points = keyframe_time_points(scenes.get("scenes") or [], cuts.get("cuts") or [], info, args)
            visual_items, segment_items, keyframe_warnings = write_keyframes(source_video, info, workspace, keyframes_dir, points)
            result["warnings"].extend(keyframe_warnings)
            common = {
                "tool": TOOL_NAME,
                "tool_version": TOOL_VERSION,
                "source_video_path": relpath(source_video, workspace),
                "duration_seconds": round(info.duration_seconds, 3),
                "fps": round(info.fps, 3),
                "frame_count": info.frame_count,
                "width": info.width,
                "height": info.height,
                "created_at": now_iso(),
            }
            visual_keyframes = {"schema_version": "analysis_v1_visual_keyframes_0.1", **common, "items": visual_items}
            segment_keyframes = {"schema_version": "analysis_v1_segment_keyframes_0.1", **common, "items": segment_items}
            finalize_outputs(workspace, cuts, scenes, visual_keyframes, segment_keyframes, state, result, reused=False)
    except BlockedError as exc:
        add_block(result, exc.code, exc.message)
    except PermissionError as exc:
        add_block(result, "workspace_permission_denied", f"Cannot read/write Analysis_V1 workspace. Original error: {exc}")
    except Exception as exc:
        result["status"] = "failed"
        result["warnings"].append({"code": "unexpected_error", "message": str(exc)})

    result["updated_at"] = now_iso()
    result["warnings"].extend(scan_for_sensitive_output(result))
    try:
        if workspace.exists() and workspace.is_dir():
            (workspace / f"{TOOL_DIR_NAME}/Report").mkdir(parents=True, exist_ok=True)
            write_json(workspace / REPORT_RESULT_REL, result)
    except Exception as exc:
        result["warnings"].append({"code": "result_write_failed", "message": str(exc)})
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Run Analysis_V1 PySceneDetect and extract baseline visual keyframes.")
    parser.add_argument("--workspace", default="", help="Analysis_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--detectors", nargs="+", choices=sorted(SUPPORTED_DETECTORS), default=DEFAULT_DETECTORS, help="PySceneDetect detectors to run.")
    parser.add_argument("--content-threshold", type=float, default=27.0, help="ContentDetector threshold.")
    parser.add_argument("--adaptive-threshold", type=float, default=3.0, help="AdaptiveDetector adaptive_threshold.")
    parser.add_argument("--threshold-threshold", type=float, default=12.0, help="ThresholdDetector threshold.")
    parser.add_argument("--merge-window-seconds", type=float, default=0.35, help="Merge cuts from different detectors within this time window.")
    parser.add_argument("--min-scene-seconds", type=float, default=0.5, help="Minimum scene duration in seconds.")
    parser.add_argument("--boundary-window-seconds", type=float, default=0.25, help="Offset used for before/after cut keyframes.")
    parser.add_argument("--force", action="store_true", help="Reset this tool's own outputs and rerun from a clean state.")
    parser.add_argument("--resume", action="store_true", help="Reuse completed output when the prepared input fingerprint and detector signature match.")
    parser.add_argument("--print-json", action="store_true", help="Print Result.json payload to stdout.")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        detectors=list(ns.detectors or DEFAULT_DETECTORS),
        content_threshold=float(ns.content_threshold),
        adaptive_threshold=float(ns.adaptive_threshold),
        threshold_threshold=float(ns.threshold_threshold),
        merge_window_seconds=float(ns.merge_window_seconds),
        min_scene_seconds=float(ns.min_scene_seconds),
        boundary_window_seconds=float(ns.boundary_window_seconds),
        force=bool(ns.force),
        resume=bool(ns.resume),
        print_json=bool(ns.print_json),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    result = run(args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} {result['status']}: {result.get('outputs', {}).get('scene_segments', '')}")
    return 0 if result["status"] in {"completed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
