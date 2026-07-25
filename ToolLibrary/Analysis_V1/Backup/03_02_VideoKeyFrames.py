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


TOOL_NAME = "03_02_VideoKeyFrames"
TOOL_VERSION = "0.1.0"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
DEFAULT_SOURCE_VIDEO_REL = f"{CONTEXT_DIR_NAME}/Video_Source.mp4"
DEFAULT_METADATA_REL = f"{CONTEXT_DIR_NAME}/Video_Metadata.json"
TOOL_DIR_NAME = "S3_02_03_02_VideoKeyFrames"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_METADATA_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_1_Video_Metadata.json"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
WORKING_BASE_KEYFRAMES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_3_visual_keyframes.json"
WORKING_BASE_SEGMENT_KEYFRAMES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_3_segment_keyframes.json"
OUTPUT_VISUAL_BOUNDARY_REL = f"{TOOL_DIR_NAME}/Output/visual_boundary_candidates.json"
OUTPUT_SEPARATOR_REL = f"{TOOL_DIR_NAME}/Output/separator_candidates.json"
OUTPUT_VISUAL_KEYFRAMES_ENHANCED_REL = f"{TOOL_DIR_NAME}/Output/visual_keyframes_enhanced.json"
OUTPUT_SEGMENT_KEYFRAMES_ENHANCED_REL = f"{TOOL_DIR_NAME}/Output/segment_keyframes_enhanced.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SESSION_VISUAL_DIR_REL = "SessionOutput/visual"
SESSION_SCENE_CUTS_REL = f"{SESSION_VISUAL_DIR_REL}/scene_cuts.json"
SESSION_SCENE_SEGMENTS_REL = f"{SESSION_VISUAL_DIR_REL}/scene_segments.json"
SESSION_VISUAL_KEYFRAMES_REL = f"{SESSION_VISUAL_DIR_REL}/visual_keyframes.json"
SESSION_SEGMENT_KEYFRAMES_REL = f"{SESSION_VISUAL_DIR_REL}/segment_keyframes.json"
SESSION_VISUAL_BOUNDARY_REL = f"{SESSION_VISUAL_DIR_REL}/visual_boundary_candidates.json"
SESSION_SEPARATOR_REL = f"{SESSION_VISUAL_DIR_REL}/separator_candidates.json"
SESSION_VISUAL_KEYFRAMES_ENHANCED_REL = f"{SESSION_VISUAL_DIR_REL}/visual_keyframes_enhanced.json"
SESSION_SEGMENT_KEYFRAMES_ENHANCED_REL = f"{SESSION_VISUAL_DIR_REL}/segment_keyframes_enhanced.json"
SESSION_KEYFRAMES_DIR_REL = f"{SESSION_VISUAL_DIR_REL}/keyframes"
GENERATED_JPG_PREFIXES = ("visual_boundary_", "separator_")
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
    sample_fps: float
    frame_diff_threshold: float
    hist_change_threshold: float
    brightness_delta_threshold: float
    edge_delta_threshold: float
    black_threshold: float
    bright_threshold: float
    solid_color_std_threshold: float
    title_card_color_std_threshold: float
    low_edge_threshold: float
    medium_edge_threshold: float
    title_card_edge_threshold: float
    info_insert_edge_threshold: float
    info_insert_std_threshold: float
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
        raise BlockedError("variables_missing", f"Required SessionContext file is missing: {VARIABLES_REL}.")
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
        raise BlockedError("video_metadata_missing", f"Required video metadata is missing: {raw}. Run 01 first.")
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
        raise BlockedError("source_video_missing", f"Source video is missing: {raw}. Run 00 first.")
    if not path.is_file():
        raise BlockedError("source_video_not_file", f"Source video is not a file: {raw}")
    return path


def require_payload(workspace: Path, rel: str, key: str) -> dict[str, Any]:
    path = workspace / rel
    if not path.exists():
        raise BlockedError("step03_output_missing", f"Required 03 output is missing: {rel}. Run 03_VideoPySceneDetect.py first.")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("step03_output_invalid", f"{rel} must contain a JSON object.")
    if not isinstance(payload.get(key), list):
        raise BlockedError("step03_output_invalid", f"{rel} must contain a list field named {key}.")
    return payload


def ensure_tool_dirs(workspace: Path) -> None:
    for rel in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", f"{TOOL_DIR_NAME}/Report"):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def source_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "fingerprint": hashlib.sha256(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")).hexdigest(),
    }


def file_signature(workspace: Path, rels: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in rels:
        path = workspace / rel
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def config_signature(args: Args, input_signature: str) -> str:
    payload = {
        "input_signature": input_signature,
        "sample_fps": float(args.sample_fps),
        "frame_diff_threshold": float(args.frame_diff_threshold),
        "hist_change_threshold": float(args.hist_change_threshold),
        "brightness_delta_threshold": float(args.brightness_delta_threshold),
        "edge_delta_threshold": float(args.edge_delta_threshold),
        "black_threshold": float(args.black_threshold),
        "bright_threshold": float(args.bright_threshold),
        "solid_color_std_threshold": float(args.solid_color_std_threshold),
        "title_card_color_std_threshold": float(args.title_card_color_std_threshold),
        "low_edge_threshold": float(args.low_edge_threshold),
        "medium_edge_threshold": float(args.medium_edge_threshold),
        "title_card_edge_threshold": float(args.title_card_edge_threshold),
        "info_insert_edge_threshold": float(args.info_insert_edge_threshold),
        "info_insert_std_threshold": float(args.info_insert_std_threshold),
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


def frame_at_time(cap: Any, fps: float, time_seconds: float) -> tuple[int, Any] | tuple[None, None]:
    if cv2 is None:
        raise BlockedError("opencv_missing", "opencv-python is required for 03_02 keyframe enhancement.")
    frame_index = max(0, int(round(time_seconds * fps)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = cap.read()
    if not ok:
        return None, None
    return frame_index, frame


def frame_metrics(frame: Any) -> dict[str, Any]:
    if cv2 is None:
        raise BlockedError("opencv_missing", "opencv-python is required for 03_02 keyframe enhancement.")
    import numpy as np  # type: ignore

    small = cv2.resize(frame, (160, 284))
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


def public_metrics(metrics: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, float]:
    if cv2 is None:
        raise BlockedError("opencv_missing", "opencv-python is required for 03_02 keyframe enhancement.")
    import numpy as np  # type: ignore

    frame_diff = 0.0
    hist_change = 0.0
    brightness_delta = 0.0
    edge_delta = 0.0
    if previous is not None:
        frame_diff = float(np.mean(cv2.absdiff(metrics["gray"], previous["gray"])))
        hist_change = float(1.0 - cv2.compareHist(metrics["hist"], previous["hist"], cv2.HISTCMP_CORREL))
        brightness_delta = abs(float(metrics["brightness"]) - float(previous["brightness"]))
        edge_delta = abs(float(metrics["edge_density"]) - float(previous["edge_density"]))
    return {
        "brightness": round(float(metrics["brightness"]), 3),
        "brightness_std": round(float(metrics["brightness_std"]), 3),
        "color_std": round(float(metrics["color_std"]), 3),
        "edge_density": round(float(metrics["edge_density"]), 5),
        "frame_diff_score": round(frame_diff, 3),
        "hist_change_score": round(hist_change, 5),
        "brightness_delta": round(brightness_delta, 3),
        "edge_delta": round(edge_delta, 5),
    }


def scan_frame_changes(video_path: Path, info: VideoInfo, args: Args) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if cv2 is None:
        raise BlockedError("opencv_missing", "opencv-python is required for 03_02 keyframe enhancement.")
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise BlockedError("opencv_video_open_failed", f"Failed to open video with OpenCV: {video_path}")
    step = max(1.0 / max(float(args.sample_fps), 0.1), 1.0 / max(info.fps, 1.0))
    samples: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    t = 0.0
    while t <= info.duration_seconds:
        frame_index, frame = frame_at_time(cap, info.fps, t)
        if frame is None or frame_index is None:
            break
        metrics = frame_metrics(frame)
        metrics_public = public_metrics(metrics, previous)
        sample = {"time": round(t, 3), "frame": int(frame_index), "metrics": metrics_public}
        samples.append(sample)
        reasons = []
        if metrics_public["frame_diff_score"] >= args.frame_diff_threshold:
            reasons.append("high_frame_diff")
        if metrics_public["hist_change_score"] >= args.hist_change_threshold:
            reasons.append("high_hist_change")
        if metrics_public["brightness_delta"] >= args.brightness_delta_threshold:
            reasons.append("brightness_delta")
        if metrics_public["edge_delta"] >= args.edge_delta_threshold:
            reasons.append("edge_delta")
        if reasons:
            confidence = min(0.95, 0.55 + 0.1 * len(reasons) + min(metrics_public["frame_diff_score"] / 100.0, 0.2) + min(metrics_public["hist_change_score"] / 2.0, 0.1))
            candidates.append({
                "index": len(candidates) + 1,
                "time": sample["time"],
                "frame": sample["frame"],
                "type": "visual_change",
                "confidence": round(confidence, 3),
                "reason": "+".join(reasons),
                "metrics": metrics_public,
            })
        previous = metrics
        t += step
    cap.release()
    return samples, candidates


def detect_separators(samples: list[dict[str, Any]], args: Args) -> list[dict[str, Any]]:
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


def save_frame(video_path: Path, fps: float, time_seconds: float, output_path: Path) -> tuple[int, str] | None:
    if cv2 is None:
        raise BlockedError("opencv_missing", "opencv-python is required for 03_02 keyframe enhancement.")
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


def save_evidence_frame(video_path: Path, info: VideoInfo, workspace: Path, keyframes_dir: Path, prefix: str, index: int, time_value: float) -> dict[str, Any] | None:
    filename = f"{prefix}_{index:04d}_t{time_value:.3f}.jpg"
    output = keyframes_dir / filename
    saved = save_frame(video_path, info.fps, time_value, output)
    if not saved:
        return None
    frame_index, path = saved
    return {"time": round(time_value, 3), "frame": frame_index, "path": relpath(Path(path), workspace)}


def add_evidence_frames(video_path: Path, info: VideoInfo, workspace: Path, keyframes_dir: Path, visual_candidates: list[dict[str, Any]], separators: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    new_keyframes: list[dict[str, Any]] = []
    new_segments: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []

    for candidate in visual_candidates:
        saved = save_evidence_frame(video_path, info, workspace, keyframes_dir, "visual_boundary", int(candidate["index"]), float(candidate["time"]))
        if not saved:
            warnings.append({"code": "visual_boundary_keyframe_failed", "message": f"Could not extract visual boundary keyframe at {float(candidate['time']):.3f}s."})
            continue
        candidate["evidence_frame"] = saved["path"]
        item = {
            "source": "visual_boundary",
            "segment_index": int(candidate["index"]),
            "role": "evidence_frame",
            "candidate_type": candidate.get("type"),
            **saved,
        }
        new_keyframes.append(item)
        new_segments.append({"segment_source": "visual_boundary", "segment_index": int(candidate["index"]), "keyframes": [{"role": "evidence_frame", **saved}]})

    for separator in separators:
        saved = save_evidence_frame(video_path, info, workspace, keyframes_dir, "separator", int(separator["index"]), float(separator["time"]))
        if not saved:
            warnings.append({"code": "separator_keyframe_failed", "message": f"Could not extract separator keyframe at {float(separator['time']):.3f}s."})
            continue
        separator["evidence_frame"] = saved["path"]
        item = {
            "source": "separator",
            "segment_index": int(separator["index"]),
            "role": "evidence_frame",
            "candidate_type": separator.get("type"),
            **saved,
        }
        new_keyframes.append(item)
        new_segments.append({"segment_source": "separator", "segment_index": int(separator["index"]), "keyframes": [{"role": "evidence_frame", **saved}]})
    return new_keyframes, new_segments, warnings


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": False,
        "reads_session_context": [VARIABLES_REL, DEFAULT_METADATA_REL, DEFAULT_SOURCE_VIDEO_REL],
        "reads_session_output": [
            SESSION_SCENE_CUTS_REL,
            SESSION_SCENE_SEGMENTS_REL,
            SESSION_VISUAL_KEYFRAMES_REL,
            SESSION_SEGMENT_KEYFRAMES_REL,
        ],
        "writes_session_context": [],
        "writes_session_output": [
            SESSION_VISUAL_BOUNDARY_REL,
            SESSION_SEPARATOR_REL,
            SESSION_VISUAL_KEYFRAMES_ENHANCED_REL,
            SESSION_SEGMENT_KEYFRAMES_ENHANCED_REL,
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


def cleanup_generated_jpgs(workspace: Path, result: dict[str, Any]) -> None:
    keyframes_dir = workspace / SESSION_KEYFRAMES_DIR_REL
    removed = 0
    if keyframes_dir.exists():
        for path in keyframes_dir.glob("*.jpg"):
            if path.name.startswith(GENERATED_JPG_PREFIXES):
                path.unlink()
                removed += 1
    if removed:
        result.setdefault("cleanup_actions", []).append({"path": f"{SESSION_KEYFRAMES_DIR_REL}/visual_boundary_*.jpg|separator_*.jpg", "action": f"removed_{removed}_files_for_force_rerun"})


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    cleanup_actions = result.setdefault("cleanup_actions", [])
    for rel in (
        TOOL_DIR_NAME,
        SESSION_VISUAL_BOUNDARY_REL,
        SESSION_SEPARATOR_REL,
        SESSION_VISUAL_KEYFRAMES_ENHANCED_REL,
        SESSION_SEGMENT_KEYFRAMES_ENHANCED_REL,
    ):
        path = workspace / rel
        if path.exists():
            remove_path(path)
            cleanup_actions.append({"path": rel, "action": "removed_for_force_rerun"})
    cleanup_generated_jpgs(workspace, result)


def prepare_inputs(workspace: Path, variables: dict[str, Any], metadata: dict[str, Any], base_visual: dict[str, Any], base_segment: dict[str, Any], source_video: Path, source_info: dict[str, Any], signature: str, input_signature: str, result: dict[str, Any]) -> dict[str, Any]:
    ensure_tool_dirs(workspace)
    for rel in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", f"{TOOL_DIR_NAME}/Report"):
        result.setdefault("prepared_directories", []).append(rel)
    write_json(workspace / WORKING_VARIABLES_REL, variables)
    write_json(workspace / WORKING_METADATA_REL, metadata)
    write_json(workspace / WORKING_BASE_KEYFRAMES_REL, base_visual)
    write_json(workspace / WORKING_BASE_SEGMENT_KEYFRAMES_REL, base_segment)
    state = {
        "tool": TOOL_NAME,
        "status": "ready",
        "phase": "prepare",
        "source": source_info,
        "input_signature": input_signature,
        "config_signature": signature,
        "inputs": {
            "variables": WORKING_VARIABLES_REL,
            "video_metadata": WORKING_METADATA_REL,
            "source_video": relpath(source_video, workspace),
            "base_visual_keyframes": WORKING_BASE_KEYFRAMES_REL,
            "base_segment_keyframes": WORKING_BASE_SEGMENT_KEYFRAMES_REL,
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
    return [str(item["path"]) for item in items if isinstance(item, dict) and item.get("path")]


def load_reusable_outputs(workspace: Path, source_info: dict[str, Any], signature: str, input_signature: str, force: bool) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    if force:
        return None
    paths = [
        workspace / OUTPUT_VISUAL_BOUNDARY_REL,
        workspace / OUTPUT_SEPARATOR_REL,
        workspace / OUTPUT_VISUAL_KEYFRAMES_ENHANCED_REL,
        workspace / OUTPUT_SEGMENT_KEYFRAMES_ENHANCED_REL,
        workspace / WORKING_STATE_REL,
    ]
    if not all(path.exists() for path in paths):
        return None
    try:
        state = read_json(workspace / WORKING_STATE_REL)
        visual_candidates = read_json(workspace / OUTPUT_VISUAL_BOUNDARY_REL)
        separators = read_json(workspace / OUTPUT_SEPARATOR_REL)
        enhanced_keyframes = read_json(workspace / OUTPUT_VISUAL_KEYFRAMES_ENHANCED_REL)
        enhanced_segment_keyframes = read_json(workspace / OUTPUT_SEGMENT_KEYFRAMES_ENHANCED_REL)
    except Exception:
        return None
    if state.get("status") != "completed":
        return None
    if (state.get("source") or {}).get("fingerprint") != source_info.get("fingerprint"):
        return None
    if state.get("input_signature") != input_signature or state.get("config_signature") != signature:
        return None
    for rel in payload_keyframe_paths(enhanced_keyframes):
        if not (workspace / rel).exists():
            return None
    return visual_candidates, separators, enhanced_keyframes, enhanced_segment_keyframes


def finalize_outputs(workspace: Path, visual_candidates: dict[str, Any], separators: dict[str, Any], enhanced_keyframes: dict[str, Any], enhanced_segment_keyframes: dict[str, Any], state: dict[str, Any], result: dict[str, Any], reused: bool) -> None:
    write_json(workspace / OUTPUT_VISUAL_BOUNDARY_REL, visual_candidates)
    write_json(workspace / OUTPUT_SEPARATOR_REL, separators)
    write_json(workspace / OUTPUT_VISUAL_KEYFRAMES_ENHANCED_REL, enhanced_keyframes)
    write_json(workspace / OUTPUT_SEGMENT_KEYFRAMES_ENHANCED_REL, enhanced_segment_keyframes)
    write_json(workspace / SESSION_VISUAL_BOUNDARY_REL, visual_candidates)
    write_json(workspace / SESSION_SEPARATOR_REL, separators)
    write_json(workspace / SESSION_VISUAL_KEYFRAMES_ENHANCED_REL, enhanced_keyframes)
    write_json(workspace / SESSION_SEGMENT_KEYFRAMES_ENHANCED_REL, enhanced_segment_keyframes)
    state = {
        **state,
        "status": "completed",
        "phase": "finalize",
        "outputs": {
            "visual_boundary_candidates": OUTPUT_VISUAL_BOUNDARY_REL,
            "separator_candidates": OUTPUT_SEPARATOR_REL,
            "visual_keyframes_enhanced": OUTPUT_VISUAL_KEYFRAMES_ENHANCED_REL,
            "segment_keyframes_enhanced": OUTPUT_SEGMENT_KEYFRAMES_ENHANCED_REL,
            "session_visual_boundary_candidates": SESSION_VISUAL_BOUNDARY_REL,
            "session_separator_candidates": SESSION_SEPARATOR_REL,
            "session_visual_keyframes_enhanced": SESSION_VISUAL_KEYFRAMES_ENHANCED_REL,
            "session_segment_keyframes_enhanced": SESSION_SEGMENT_KEYFRAMES_ENHANCED_REL,
            "session_keyframes_dir": SESSION_KEYFRAMES_DIR_REL,
        },
        "reused_completed_output": reused,
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["status"] = "completed"
    result["outputs"] = state["outputs"]
    result["counts"] = {
        "visual_boundary_candidates": len(visual_candidates.get("items") or []),
        "separator_candidates": len(separators.get("items") or []),
        "visual_keyframes_enhanced": len(enhanced_keyframes.get("items") or []),
        "segment_keyframes_enhanced": len(enhanced_segment_keyframes.get("items") or []),
    }
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_METADATA_REL,
        WORKING_BASE_KEYFRAMES_REL,
        WORKING_BASE_SEGMENT_KEYFRAMES_REL,
        WORKING_STATE_REL,
        OUTPUT_VISUAL_BOUNDARY_REL,
        OUTPUT_SEPARATOR_REL,
        OUTPUT_VISUAL_KEYFRAMES_ENHANCED_REL,
        OUTPUT_SEGMENT_KEYFRAMES_ENHANCED_REL,
        SESSION_VISUAL_BOUNDARY_REL,
        SESSION_SEPARATOR_REL,
        SESSION_VISUAL_KEYFRAMES_ENHANCED_REL,
        SESSION_SEGMENT_KEYFRAMES_ENHANCED_REL,
        REPORT_RESULT_REL,
    ]
    if reused:
        result["warnings"].append({"code": "reused_completed_output", "message": "Existing enhanced keyframe output was reused because the input fingerprint and parameter signature matched."})


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
        require_payload(workspace, SESSION_SCENE_CUTS_REL, "cuts")
        require_payload(workspace, SESSION_SCENE_SEGMENTS_REL, "scenes")
        base_visual = require_payload(workspace, SESSION_VISUAL_KEYFRAMES_REL, "items")
        base_segment = require_payload(workspace, SESSION_SEGMENT_KEYFRAMES_REL, "items")
        input_signature = file_signature(workspace, [SESSION_SCENE_CUTS_REL, SESSION_SCENE_SEGMENTS_REL, SESSION_VISUAL_KEYFRAMES_REL, SESSION_SEGMENT_KEYFRAMES_REL])
        source_info = source_fingerprint(source_video)
        signature = config_signature(args, input_signature)
        reusable = load_reusable_outputs(workspace, source_info, signature, input_signature, args.force or not args.resume)
        state = prepare_inputs(workspace, variables, metadata, base_visual, base_segment, source_video, source_info, signature, input_signature, result)
        if reusable is not None:
            visual_candidates, separators, enhanced_keyframes, enhanced_segment_keyframes = reusable
            finalize_outputs(workspace, visual_candidates, separators, enhanced_keyframes, enhanced_segment_keyframes, state, result, reused=True)
        else:
            info = metadata_video_info(metadata)
            state = {**state, "phase": "scan_visual_changes", "updated_at": now_iso()}
            write_json(workspace / WORKING_STATE_REL, state)
            samples, visual_items = scan_frame_changes(source_video, info, args)
            separator_items = detect_separators(samples, args)
            state = {**state, "phase": "extract_evidence_frames", "updated_at": now_iso()}
            write_json(workspace / WORKING_STATE_REL, state)
            new_keyframes, new_segments, warnings = add_evidence_frames(source_video, info, workspace, workspace / SESSION_KEYFRAMES_DIR_REL, visual_items, separator_items)
            result["warnings"].extend(warnings)
            common = {
                "tool": TOOL_NAME,
                "tool_version": TOOL_VERSION,
                "source_video_path": relpath(source_video, workspace),
                "duration_seconds": round(info.duration_seconds, 3),
                "fps": round(info.fps, 3),
                "frame_count": info.frame_count,
                "width": info.width,
                "height": info.height,
                "sample_fps": float(args.sample_fps),
                "created_at": now_iso(),
            }
            visual_candidates = {"schema_version": "analysis_v1_visual_boundary_candidates_0.1", **common, "items": visual_items}
            separators = {"schema_version": "analysis_v1_separator_candidates_0.1", **common, "items": separator_items}
            base_items = list(base_visual.get("items") or [])
            enhanced_items = [dict(item) for item in base_items]
            for item in new_keyframes:
                enhanced_items.append({"index": len(enhanced_items) + 1, **item})
            enhanced_keyframes = {"schema_version": "analysis_v1_visual_keyframes_enhanced_0.1", **common, "base_keyframe_count": len(base_items), "enhancement_keyframe_count": len(new_keyframes), "items": enhanced_items}
            base_segment_items = list(base_segment.get("items") or [])
            enhanced_segment_items = [dict(item) for item in base_segment_items] + new_segments
            enhanced_segment_keyframes = {"schema_version": "analysis_v1_segment_keyframes_enhanced_0.1", **common, "base_segment_keyframe_count": len(base_segment_items), "enhancement_segment_keyframe_count": len(new_segments), "items": enhanced_segment_items}
            finalize_outputs(workspace, visual_candidates, separators, enhanced_keyframes, enhanced_segment_keyframes, state, result, reused=False)
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
    parser = argparse.ArgumentParser(description="Enhance Analysis_V1 keyframes with visual boundary and separator evidence frames.")
    parser.add_argument("--workspace", default="", help="Analysis_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--sample-fps", type=float, default=2.0, help="Sampling rate for visual change scan.")
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
    parser.add_argument("--force", action="store_true", help="Reset this tool's own outputs and rerun from a clean state.")
    parser.add_argument("--resume", action="store_true", help="Reuse completed output when input fingerprint and parameter signature match.")
    parser.add_argument("--print-json", action="store_true", help="Print Result.json payload to stdout.")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        sample_fps=float(ns.sample_fps),
        frame_diff_threshold=float(ns.frame_diff_threshold),
        hist_change_threshold=float(ns.hist_change_threshold),
        brightness_delta_threshold=float(ns.brightness_delta_threshold),
        edge_delta_threshold=float(ns.edge_delta_threshold),
        black_threshold=float(ns.black_threshold),
        bright_threshold=float(ns.bright_threshold),
        solid_color_std_threshold=float(ns.solid_color_std_threshold),
        title_card_color_std_threshold=float(ns.title_card_color_std_threshold),
        low_edge_threshold=float(ns.low_edge_threshold),
        medium_edge_threshold=float(ns.medium_edge_threshold),
        title_card_edge_threshold=float(ns.title_card_edge_threshold),
        info_insert_edge_threshold=float(ns.info_insert_edge_threshold),
        info_insert_std_threshold=float(ns.info_insert_std_threshold),
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
        print(f"{TOOL_NAME} {result['status']}: {result.get('outputs', {}).get('visual_keyframes_enhanced', '')}")
    return 0 if result["status"] in {"completed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
