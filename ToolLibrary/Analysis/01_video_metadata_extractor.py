from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_binaries import find_ffmpeg, find_ffprobe, media_env

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - handled at runtime
    cv2 = None  # type: ignore


TOOL_NAME = "VideoMetadataExtractor"
TOOL_VERSION = "0.1.0"


@dataclass(frozen=True)
class VideoMetadataResult:
    tool: str
    tool_version: str
    status: str
    output_path: str
    metadata: dict[str, Any]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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


def detect_with_opencv(video_path: Path) -> dict[str, Any]:
    if cv2 is None:
        raise RuntimeError("opencv-python is not available")
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"failed to open video with OpenCV: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration = frame_count / fps if fps > 0 else 0.0
    capture.release()
    return {
        "backend": "opencv",
        "fps": fps,
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_seconds": duration,
    }


def merge_metadata(video_path: Path, opencv_data: dict[str, Any], probe_data: dict[str, Any], ffmpeg_data: dict[str, Any]) -> dict[str, Any]:
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
    codec_name = str((video_stream or {}).get("codec_name") or "")
    pix_fmt = str((video_stream or {}).get("pix_fmt") or "")
    audio_stream_count = int((probe_data.get("audio_stream_count") if probe_data.get("available") else ffmpeg_data.get("audio_stream_count")) or 0)

    return {
        "path": str(video_path),
        "filename": video_path.name,
        "duration_seconds": round(duration, 3),
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "aspect_ratio": round(width / height, 6) if height else 0.0,
        "size_bytes": int(stat.st_size),
        "size_mb": round(stat.st_size / (1024 * 1024), 2),
        "codec_name": codec_name,
        "pixel_format": pix_fmt,
        "has_audio": audio_stream_count > 0,
        "audio_stream_count": audio_stream_count,
        "audio_streams": probe_data.get("audio_streams") if probe_data.get("available") else ffmpeg_data.get("audio_streams", []),
        "source_backends": {
            "opencv": True,
            "ffprobe": bool(probe_data.get("available")),
            "ffmpeg": bool(ffmpeg_data.get("available")),
        },
        "ffprobe_error": probe_data.get("error") if not probe_data.get("available") else "",
        "ffmpeg_error": ffmpeg_data.get("error") if not ffmpeg_data.get("available") else "",
    }


def render_markdown(metadata: dict[str, Any]) -> str:
    rows = [
        ("Path", metadata.get("path")),
        ("Duration Seconds", metadata.get("duration_seconds")),
        ("FPS", metadata.get("fps")),
        ("Frame Count", metadata.get("frame_count")),
        ("Resolution", f"{metadata.get('width')}x{metadata.get('height')}"),
        ("Aspect Ratio", metadata.get("aspect_ratio")),
        ("Size MB", metadata.get("size_mb")),
        ("Codec", metadata.get("codec_name") or "unknown"),
        ("Pixel Format", metadata.get("pixel_format") or "unknown"),
        ("Has Audio", metadata.get("has_audio")),
        ("Audio Streams", metadata.get("audio_stream_count")),
    ]
    lines = ["# Video Metadata", ""]
    for label, value in rows:
        lines.append(f"- {label}: {value}")
    lines.append("")
    return "\n".join(lines)


def resolve_output_dir(workspace: Path | None, output_dir: Path | None) -> Path:
    if output_dir is not None:
        return output_dir
    if workspace is None:
        return Path.cwd() / "meta"
    return workspace / "meta"


def stage_source_video(video_path: Path, workspace: Path | None) -> dict[str, Any]:
    if workspace is None:
        return {"ready": False, "action": "skipped_no_workspace", "path": ""}

    resolved_workspace = workspace.expanduser().resolve()
    target = resolved_workspace / "source_video.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and target.resolve() == video_path:
        return {"ready": True, "action": "skipped_same_file", "path": str(target)}

    if target.exists() and target.stat().st_size == video_path.stat().st_size:
        return {"ready": True, "action": "skipped_existing_same_size", "path": str(target)}

    shutil.copy2(video_path, target)
    return {"ready": True, "action": "copied", "path": str(target)}


def extract_video_metadata(video_path: Path, workspace: Path | None = None, output_dir: Path | None = None) -> VideoMetadataResult:
    video_path = video_path.expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"video file does not exist: {video_path}")
    if not video_path.is_file():
        raise ValueError(f"video path is not a file: {video_path}")

    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    resolved_output_dir = resolve_output_dir(resolved_workspace, output_dir.expanduser().resolve() if output_dir else None)
    source_video = stage_source_video(video_path, resolved_workspace)
    opencv_data = detect_with_opencv(video_path)
    probe_data = ffprobe_metadata(video_path)
    ffmpeg_data = {} if probe_data.get("available") else ffmpeg_stream_metadata(video_path)
    metadata = merge_metadata(video_path, opencv_data, probe_data, ffmpeg_data)
    metadata["workspace_source_video_ready"] = bool(source_video.get("ready"))
    metadata["workspace_source_video_action"] = source_video.get("action") or ""
    metadata["workspace_source_video_path"] = source_video.get("path") or ""
    result = VideoMetadataResult(
        tool=TOOL_NAME,
        tool_version=TOOL_VERSION,
        status="completed",
        output_path=str(resolved_output_dir / "video_metadata.json"),
        metadata=metadata,
    )

    write_json(resolved_output_dir / "video_metadata.json", metadata)
    write_json(resolved_output_dir / "run_result.json", result.__dict__)
    write_text(resolved_output_dir / "video_metadata.md", render_markdown(metadata))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract video metadata for an OpenCrew/OpenClip task workspace.")
    parser.add_argument("--video", required=True, help="Path to the input video file.")
    parser.add_argument("--workspace", help="Task workspace path. If --output-dir is omitted, writes to <workspace>/meta.")
    parser.add_argument("--output-dir", help="Explicit output directory. Overrides --workspace and keeps the tool directory-agnostic.")
    parser.add_argument("--print-json", action="store_true", help="Print the tool result JSON to stdout.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    workspace = Path(args.workspace) if args.workspace else None
    output_dir = Path(args.output_dir) if args.output_dir else None
    result = extract_video_metadata(Path(args.video), workspace=workspace, output_dir=output_dir)
    if args.print_json:
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} completed: {result.output_path}")


if __name__ == "__main__":
    main()
