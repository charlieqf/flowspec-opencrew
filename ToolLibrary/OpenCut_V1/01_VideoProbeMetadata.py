from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLLIB_ROOT = REPO_ROOT / "ToolLibrary"
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))

try:
    from OpenCut_V1.core.media_binaries import find_ffmpeg, find_ffprobe, media_env
except Exception:  # pragma: no cover - standalone fallback when package import path is unusual
    OPENCUT_DIR = Path(__file__).resolve().parent
    if str(OPENCUT_DIR) not in sys.path:
        sys.path.insert(0, str(OPENCUT_DIR))
    from core.media_binaries import find_ffmpeg, find_ffprobe, media_env  # type: ignore

TOOL_NAME = "01_VideoProbeMetadata"
TOOL_VERSION = "0.1.0"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
DEFAULT_SOURCE_VIDEO_REL = f"{CONTEXT_DIR_NAME}/Video_Source.mp4"
CONTEXT_METADATA_REL = f"{CONTEXT_DIR_NAME}/Video_Metadata.json"
TOOL_DIR_NAME = "S2_01_VideoProbeMetadata"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
OUTPUT_METADATA_REL = f"{TOOL_DIR_NAME}/Output/Video_Metadata.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SUPPORTED_SOURCE_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
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
    force: bool
    resume: bool
    print_json: bool


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


def parse_fraction(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "/" not in text:
        try:
            return float(text)
        except ValueError:
            return None
    numerator_text, denominator_text = text.split("/", 1)
    try:
        numerator = float(numerator_text)
        denominator = float(denominator_text)
    except ValueError:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, env=media_env())


def ffprobe_metadata(video_path: Path) -> dict[str, Any]:
    try:
        result = run_command([
            find_ffprobe(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ])
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    if result.returncode != 0:
        return {"available": False, "error": (result.stderr or result.stdout or "ffprobe failed").strip()}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"available": False, "error": f"failed to parse ffprobe output: {exc}"}

    video_stream = next((stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"), {})
    audio_streams = [stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"]
    return {
        "available": True,
        "format": payload.get("format") or {},
        "video_stream": video_stream,
        "audio_stream_count": len(audio_streams),
        "audio_streams": audio_streams,
    }


def ffmpeg_stream_metadata(video_path: Path) -> dict[str, Any]:
    try:
        result = run_command([find_ffmpeg(), "-hide_banner", "-i", str(video_path)])
    except Exception as exc:
        return {"available": False, "error": str(exc)}

    output = result.stderr or result.stdout or ""
    if not output:
        return {"available": False, "error": "ffmpeg produced no stream output"}

    video_stream: dict[str, Any] = {}
    audio_streams: list[dict[str, Any]] = []
    duration_seconds = None
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration_seconds = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    for line in output.splitlines():
        text = line.strip()
        if " Video: " in text and not video_stream:
            codec_match = re.search(r"Video:\s*([^,]+)", text)
            pixel_match = re.search(r"Video:\s*[^,]+,\s*([A-Za-z0-9_]+(?:\([^)]*\))?)", text)
            size_match = re.search(r"(\d{2,5})x(\d{2,5})", text)
            fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", text)
            video_stream = {
                "codec_name": codec_match.group(1).strip() if codec_match else "",
                "pix_fmt": pixel_match.group(1).strip() if pixel_match else "",
                "width": int(size_match.group(1)) if size_match else 0,
                "height": int(size_match.group(2)) if size_match else 0,
                "avg_frame_rate": fps_match.group(1) if fps_match else "",
                "duration": duration_seconds,
            }
        elif " Audio: " in text:
            codec_match = re.search(r"Audio:\s*([^,]+)", text)
            sample_rate_match = re.search(r"(\d+)\s*Hz", text)
            channel_match = re.search(r"Hz,\s*([^,]+)", text)
            audio_streams.append({
                "codec_name": codec_match.group(1).strip() if codec_match else "",
                "sample_rate": int(sample_rate_match.group(1)) if sample_rate_match else 0,
                "channels": channel_match.group(1).strip() if channel_match else "",
            })

    return {
        "available": bool(video_stream or audio_streams),
        "format": {"duration": duration_seconds} if duration_seconds is not None else {},
        "video_stream": video_stream,
        "audio_stream_count": len(audio_streams),
        "audio_streams": audio_streams,
        "error": "" if video_stream or audio_streams else "failed to parse ffmpeg stream output",
    }


def detect_with_opencv(video_path: Path) -> dict[str, Any]:
    if os.environ.get("OPENCREW_OPENCUT_V1_USE_OPENCV_METADATA") != "1":
        return {"available": False, "error": "opencv metadata probe is disabled; ffprobe/ffmpeg are the default backends"}
    try:
        import cv2  # type: ignore
    except Exception as exc:
        return {"available": False, "error": f"opencv-python is not available: {exc}"}
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            return {"available": False, "error": f"failed to open video with OpenCV: {video_path}"}
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = frame_count / fps if fps > 0 else 0.0
        return {
            "available": True,
            "backend": "opencv",
            "fps": fps,
            "frame_count": frame_count,
            "width": width,
            "height": height,
            "duration_seconds": duration,
        }
    finally:
        capture.release()


def source_fingerprint(video_path: Path) -> dict[str, Any]:
    stat = video_path.stat()
    return {
        "path": str(video_path),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "fingerprint": hashlib.sha256(f"{video_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")).hexdigest(),
    }


def merge_metadata(workspace: Path, video_path: Path, opencv_data: dict[str, Any], probe_data: dict[str, Any], ffmpeg_data: dict[str, Any]) -> dict[str, Any]:
    stat = video_path.stat()
    stream_source = probe_data if probe_data.get("available") else ffmpeg_data
    video_stream = stream_source.get("video_stream") if stream_source.get("available") else {}
    format_data = stream_source.get("format") if stream_source.get("available") else {}

    probe_fps = parse_fraction((video_stream or {}).get("avg_frame_rate")) or parse_fraction((video_stream or {}).get("r_frame_rate"))
    probe_duration = None
    for candidate in [(format_data or {}).get("duration"), (video_stream or {}).get("duration")]:
        try:
            probe_duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue

    fps = float(opencv_data.get("fps") or probe_fps or 0.0)
    frame_count = int(opencv_data.get("frame_count") or int((video_stream or {}).get("nb_frames") or 0) or 0)
    duration = float(opencv_data.get("duration_seconds") or probe_duration or 0.0)
    if duration <= 0 and frame_count > 0 and fps > 0:
        duration = frame_count / fps

    width = int(opencv_data.get("width") or int((video_stream or {}).get("width") or 0) or 0)
    height = int(opencv_data.get("height") or int((video_stream or {}).get("height") or 0) or 0)
    audio_stream_count = int((probe_data.get("audio_stream_count") if probe_data.get("available") else ffmpeg_data.get("audio_stream_count")) or 0)

    return {
        "schema_version": "opencut_v1_video_metadata_0.1",
        "source_video_path": relpath(video_path, workspace),
        "filename": video_path.name,
        "duration_seconds": round(duration, 3),
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "aspect_ratio": round(width / height, 6) if height else 0.0,
        "size_bytes": int(stat.st_size),
        "size_mb": round(stat.st_size / (1024 * 1024), 2),
        "codec_name": str((video_stream or {}).get("codec_name") or ""),
        "pixel_format": str((video_stream or {}).get("pix_fmt") or ""),
        "has_audio": audio_stream_count > 0,
        "audio_stream_count": audio_stream_count,
        "audio_streams": probe_data.get("audio_streams") if probe_data.get("available") else ffmpeg_data.get("audio_streams", []),
        "source_backends": {
            "opencv": bool(opencv_data.get("available")),
            "ffprobe": bool(probe_data.get("available")),
            "ffmpeg": bool(ffmpeg_data.get("available")),
        },
        "backend_errors": {
            "opencv": opencv_data.get("error") if not opencv_data.get("available") else "",
            "ffprobe": probe_data.get("error") if not probe_data.get("available") else "",
            "ffmpeg": ffmpeg_data.get("error") if not ffmpeg_data.get("available") else "",
        },
        "created_at": now_iso(),
    }


def has_minimum_metadata(metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("duration_seconds")) and bool(metadata.get("fps")) and bool(metadata.get("frame_count"))


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
        raise BlockedError("variables_missing", f"Required SessionContext file is missing: {VARIABLES_REL}")
    try:
        variables = read_json(path)
    except Exception as exc:
        raise BlockedError("variables_invalid", f"Cannot read {VARIABLES_REL}: {exc}") from exc
    if not isinstance(variables, dict):
        raise BlockedError("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    return variables


def resolve_source_video(workspace: Path, variables: dict[str, Any]) -> Path:
    source_rel = str(variables.get("source_video_path") or DEFAULT_SOURCE_VIDEO_REL).strip()
    if not source_rel:
        raise BlockedError("source_video_path_missing", "Variables.json has no source_video_path.")
    source = Path(source_rel)
    if source.is_absolute():
        raise BlockedError("source_video_path_not_relative", "OpenCut_V1 tools must use a workspace-relative source_video_path.")
    source = workspace / source
    if not source.exists():
        raise BlockedError("source_video_missing", f"Source video is missing: {source_rel}")
    if not source.is_file():
        raise BlockedError("source_video_not_file", f"Source video is not a file: {source_rel}")
    source_suffix = source.suffix.lower()
    if source_suffix not in SUPPORTED_SOURCE_VIDEO_EXTS:
        allowed = ", ".join(sorted(SUPPORTED_SOURCE_VIDEO_EXTS))
        raise BlockedError("source_video_unsupported_format", f"Source video must use one of these formats: {allowed}. Got: {source_rel}")
    return source


def ensure_tool_dirs(workspace: Path) -> None:
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        f"{TOOL_DIR_NAME}/Report",
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def remove_video_metadata_pointer(workspace: Path) -> None:
    variables_path = workspace / VARIABLES_REL
    if not variables_path.exists():
        return
    try:
        variables = read_json(variables_path)
    except Exception:
        return
    if not isinstance(variables, dict) or "video_metadata_path" not in variables:
        return
    variables.pop("video_metadata_path", None)
    variables["updated_at"] = now_iso()
    write_json(variables_path, variables)


def update_video_metadata_pointer(workspace: Path) -> None:
    variables_path = workspace / VARIABLES_REL
    variables = read_json(variables_path)
    if not isinstance(variables, dict):
        raise BlockedError("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    variables["video_metadata_path"] = CONTEXT_METADATA_REL
    variables["updated_at"] = now_iso()
    write_json(variables_path, variables)


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    cleanup_actions = result.setdefault("cleanup_actions", [])
    tool_dir = workspace / TOOL_DIR_NAME
    if tool_dir.exists():
        remove_path(tool_dir)
        cleanup_actions.append({"path": TOOL_DIR_NAME, "action": "removed_for_force_rerun"})
    context_metadata = workspace / CONTEXT_METADATA_REL
    if context_metadata.exists():
        remove_path(context_metadata)
        cleanup_actions.append({"path": CONTEXT_METADATA_REL, "action": "removed_for_force_rerun"})
    remove_video_metadata_pointer(workspace)


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": False,
        "reads_session_context": [VARIABLES_REL, DEFAULT_SOURCE_VIDEO_REL],
        "writes_session_context": [CONTEXT_METADATA_REL, f"{VARIABLES_REL}:video_metadata_path"],
        "created_files": [],
        "prepared_directories": [],
        "cleanup_actions": [],
        "inputs": {},
        "outputs": {},
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


def prepare_inputs(workspace: Path, variables: dict[str, Any], source_video: Path, source_info: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    ensure_tool_dirs(workspace)
    prepared = result.setdefault("prepared_directories", [])
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        f"{TOOL_DIR_NAME}/Report",
    ):
        prepared.append(rel)

    write_json(workspace / WORKING_VARIABLES_REL, variables)
    state = {
        "tool": TOOL_NAME,
        "status": "ready",
        "phase": "prepare",
        "source": source_info,
        "inputs": {
            "variables": WORKING_VARIABLES_REL,
            "source_video": relpath(source_video, workspace),
        },
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["inputs"] = {
        "variables": WORKING_VARIABLES_REL,
        "source_video": relpath(source_video, workspace),
    }
    return state


def load_reusable_metadata(workspace: Path, source_info: dict[str, Any], force: bool) -> dict[str, Any] | None:
    if force:
        return None
    output = workspace / OUTPUT_METADATA_REL
    existing_state_path = workspace / WORKING_STATE_REL
    if not output.exists() or not existing_state_path.exists():
        return None
    try:
        existing_state = read_json(existing_state_path)
        metadata = read_json(output)
    except Exception:
        return None
    if existing_state.get("status") != "completed":
        return None
    if (existing_state.get("source") or {}).get("fingerprint") != source_info.get("fingerprint"):
        return None
    return metadata if isinstance(metadata, dict) else None


def probe_metadata(workspace: Path, source_video: Path) -> dict[str, Any]:
    probe_data = ffprobe_metadata(source_video)
    ffmpeg_data = {} if probe_data.get("available") else ffmpeg_stream_metadata(source_video)
    opencv_data = {"available": False, "error": "opencv metadata probe was not needed"}
    metadata = merge_metadata(workspace, source_video, opencv_data, probe_data, ffmpeg_data)
    if not has_minimum_metadata(metadata):
        opencv_data = detect_with_opencv(source_video)
        metadata = merge_metadata(workspace, source_video, opencv_data, probe_data, ffmpeg_data)
    if not has_minimum_metadata(metadata):
        raise BlockedError(
            "video_metadata_incomplete",
            "Unable to read required video metadata: duration_seconds, fps, and frame_count are required.",
        )
    return metadata


def finalize_outputs(workspace: Path, metadata: dict[str, Any], state: dict[str, Any], result: dict[str, Any], reused: bool) -> None:
    write_json(workspace / OUTPUT_METADATA_REL, metadata)
    write_json(workspace / CONTEXT_METADATA_REL, metadata)
    update_video_metadata_pointer(workspace)
    state = {
        **state,
        "status": "completed",
        "phase": "finalize",
        "outputs": {
            "metadata": OUTPUT_METADATA_REL,
            "session_context_metadata": CONTEXT_METADATA_REL,
        },
        "reused_completed_output": reused,
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["status"] = "completed"
    result["outputs"] = {
        "metadata": OUTPUT_METADATA_REL,
        "session_context_metadata": CONTEXT_METADATA_REL,
        "variables": VARIABLES_REL,
    }
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_STATE_REL,
        OUTPUT_METADATA_REL,
        CONTEXT_METADATA_REL,
        REPORT_RESULT_REL,
        VARIABLES_REL,
    ]
    if reused:
        result["warnings"].append({"code": "reused_completed_output", "message": "Existing metadata output was reused because the input fingerprint matched."})


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    try:
        validate_workspace(workspace)
        if args.force:
            force_reset(workspace, result)
        variables = load_variables(workspace)
        source_video = resolve_source_video(workspace, variables)
        source_info = source_fingerprint(source_video)
        reusable = load_reusable_metadata(workspace, source_info, args.force or not args.resume)
        state = prepare_inputs(workspace, variables, source_video, source_info, result)
        if reusable is not None:
            metadata = reusable
            reused = True
        else:
            metadata = probe_metadata(workspace, source_video)
            reused = False
        finalize_outputs(workspace, metadata, state, result, reused)
    except BlockedError as exc:
        add_block(result, exc.code, exc.message)
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
    parser = argparse.ArgumentParser(description="Probe OpenCut_V1 source video metadata from SessionContext.")
    parser.add_argument("--workspace", default="", help="OpenCut_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--force", action="store_true", help="Reset this tool's own outputs and rerun from a clean state.")
    parser.add_argument("--resume", action="store_true", help="Reuse completed output when the prepared input fingerprint matches.")
    parser.add_argument("--print-json", action="store_true", help="Print Result.json payload to stdout.")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        force=bool(ns.force),
        resume=bool(ns.resume),
        print_json=bool(ns.print_json),
    )


def main(argv: list[str] | None = None) -> int:
    cli_args = argv if argv is not None else sys.argv[1:]
    if "--tool-session-root" in cli_args:
        try:
            from ToolLibrary.OpenCut_V1.framework_bridge import maybe_run_framework_bridge
        except ModuleNotFoundError:
            repo_root = str(Path(__file__).resolve().parents[2])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from ToolLibrary.OpenCut_V1.framework_bridge import maybe_run_framework_bridge

        framework_exit = maybe_run_framework_bridge(cli_args, script_path=Path(__file__), tool_name=TOOL_NAME)
        if framework_exit is not None:
            return framework_exit

    args = parse_args(cli_args)
    result = run(args)
    exit_code = 0 if result.get("status") == "completed" else 2 if result.get("status") == "blocked" else 1
    if args.print_json or exit_code != 0:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
