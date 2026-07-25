from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from PIL import Image
except Exception:
    Image = None


TOOL_NAME = "06_01_TalkingHeadVideoPlanComposer"
TOOL_VERSION = "0.1.0"
TOOL_DIR_NAME = "S10_06_01_TalkingHeadVideoPlanComposer"
VARIABLES_REL = "SessionContext/Variables.json"
STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
EDIT_STORYBOARD_REL = "SessionOutput/storyboard/koubo_storyboard_edit.json"
PLAN_REL = "SessionOutput/storyboard/video_generation_plan.json"
EXECUTION_RESULT_REL = "SessionOutput/storyboard/video_plan_execution_result.json"
STORYBOARD_WORKING_REL = "SessionOutput/storyboard/Working"
ASSET_HISTORY_REL = "SessionOutput/storyboard/assets/history"
RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
COMPOSE_RESULT_REL = f"{TOOL_DIR_NAME}/Output/video_plan_compose_result.json"
SESSION_COMPOSE_RESULT_REL = "SessionOutput/storyboard/video_plan_compose_result.json"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
HYPERFRAME_RENDER_TIMEOUT_SECONDS = 180
WATERMARK_DETECTION_MIN_SCORE = 0.18
WATERMARK_AUTO_MIN_CONFIDENCE = 0.45
SECRET_PATTERNS = (
    "postgresql://",
    "postgresql+psycopg://",
    "password",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "bearer ",
    "cookie",
)


class ToolError(RuntimeError):
    pass


class BlockedError(ToolError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Args:
    workspace: str
    target_type: str
    shot_id: str
    scene_id: str
    subtitle_mode: str
    watermark_mode: str
    force: bool
    resume: bool
    print_json: bool = False


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_ms() -> int:
    return int(time.time() * 1000)


def text_value(value: Any) -> str:
    return str(value or "").strip()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def stable_hash(payload: Any) -> str:
    raw = json.dumps(json_safe(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def plan_hash(plan: dict[str, Any]) -> str:
    payload = json_safe(plan)
    if isinstance(payload, dict):
        payload = {key: value for key, value in payload.items() if key not in {"plan_hash", "plan_run_id", "created_at"}}
    return stable_hash(payload)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser() if raw_workspace else Path.cwd()
    try:
        return workspace.resolve()
    except Exception:
        return workspace.absolute()


def workspace_path(workspace: Path, rel_path: str) -> Path:
    path = Path(rel_path)
    return path if path.is_absolute() else workspace / path


def rel(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def safe_name(value: str, fallback: str) -> str:
    name = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in text_value(value))
    name = "_".join(part for part in name.split("_") if part)
    return name or fallback


def scene_key(shot_id: str, scene_id: str) -> str:
    return f"{safe_name(shot_id, 'shot')}_{safe_name(scene_id, 'scene')}"


def ensure_tool_dirs(workspace: Path) -> None:
    for rel_path in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        f"{TOOL_DIR_NAME}/Report",
        STORYBOARD_WORKING_REL,
    ):
        (workspace / rel_path).mkdir(parents=True, exist_ok=True)


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    tool_dir = workspace / TOOL_DIR_NAME
    if tool_dir.exists():
        remove_path(tool_dir)
        result.setdefault("cleanup_actions", []).append({"path": TOOL_DIR_NAME, "action": "removed_for_force_rerun"})


def load_required_json(workspace: Path, rel_path: str, code: str) -> dict[str, Any]:
    path = workspace / rel_path
    if not path.exists():
        raise BlockedError(code, f"Missing required file: {rel_path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError(f"{code}_invalid", f"{rel_path} must contain a JSON object.")
    return payload


def validate_video_execution_complete(workspace: Path) -> None:
    """Do not turn an upstream segment failure into a missing-file error."""

    path = workspace / EXECUTION_RESULT_REL
    if not path.is_file():
        return
    payload = read_json(path)
    if not isinstance(payload, dict):
        return
    summary = dict_value(payload.get("summary"))
    status = text_value(payload.get("status")).lower()
    failed_count = int(safe_float(summary.get("failed_count"), 0))
    if failed_count > 0 or status in {"failed", "blocked", "completed_with_failed_items"}:
        raise BlockedError(
            "video_plan_has_failed_segments",
            "存在尚未成功生成的视频片段，已停止合成；请先继续完成逐句生成视频。",
        )


def copy_inputs_to_working(workspace: Path, variables: dict[str, Any], storyboard: dict[str, Any], plan: dict[str, Any], args: Args, result: dict[str, Any]) -> None:
    files = {
        f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json": variables,
        f"{TOOL_DIR_NAME}/Working/InputFrom_7_srt_storyboard.json": storyboard,
        f"{TOOL_DIR_NAME}/Working/InputFrom_8_video_generation_plan.json": plan,
        f"{TOOL_DIR_NAME}/Working/InputParams_video_plan_composer.json": {
            "target_type": args.target_type,
            "shot_id": args.shot_id,
            "scene_id": args.scene_id,
            "subtitle_mode": args.subtitle_mode,
            "watermark_mode": args.watermark_mode,
        },
    }
    for rel_path, payload in files.items():
        write_json(workspace / rel_path, payload)
        result.setdefault("created_files", []).append(rel_path)


def validate_args(args: Args) -> None:
    if args.target_type not in {"scene", "shot", "task"}:
        raise BlockedError("target_type_invalid", "--target-type must be scene, shot, or task.")
    if args.target_type == "scene" and (not args.shot_id or not args.scene_id):
        raise BlockedError("scene_target_requires_ids", "--target-type scene requires --shot-id and --scene-id.")
    if args.target_type == "shot" and not args.shot_id:
        raise BlockedError("shot_target_requires_shot_id", "--target-type shot requires --shot-id.")
    if args.subtitle_mode not in {"hyperframe", "none"}:
        raise BlockedError("subtitle_mode_invalid", "--subtitle-mode must be hyperframe or none.")
    if args.watermark_mode not in {"auto", "always", "never"}:
        raise BlockedError("watermark_mode_invalid", "--watermark-mode must be auto, always, or never.")


def ffmpeg_executable() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    root = Path(__file__).resolve().parents[2]
    for candidate in (
        root / ".bin" / "ffmpeg",
        root / "ToolLibrary" / ".bin" / "ffmpeg",
        root / "ToolLibrary" / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffmpeg",
        root / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffmpeg",
    ):
        if candidate.exists():
            return str(candidate)
    raise ToolError("ffmpeg executable is missing.")


def ffprobe_executable() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    sibling = Path(ffmpeg_executable()).with_name("ffprobe")
    if sibling.exists():
        return str(sibling)
    root = Path(__file__).resolve().parents[2]
    for candidate in (
        root / ".bin" / "ffprobe",
        root / "ToolLibrary" / ".bin" / "ffprobe",
        root / "ToolLibrary" / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffprobe",
        root / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffprobe",
    ):
        if candidate.exists():
            return str(candidate)
    raise ToolError("ffprobe executable is missing.")


def probe_media(path: Path) -> dict[str, Any]:
    command = [
        ffprobe_executable(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise ToolError(f"ffprobe failed for {path.name}: {completed.stderr[:1200]}")
    payload = json.loads(completed.stdout or "{}")
    streams = list_value(payload.get("streams"))
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), {})
    duration = float(dict_value(payload.get("format")).get("duration") or video_stream.get("duration") or 0)
    if duration <= 0:
        raise ToolError(f"ffprobe returned invalid duration for {path.name}")
    return {
        "path": str(path),
        "duration_seconds": round(duration, 3),
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "r_frame_rate": text_value(video_stream.get("r_frame_rate")),
        "avg_frame_rate": text_value(video_stream.get("avg_frame_rate")),
        "has_audio": bool(audio_stream),
        "size_bytes": path.stat().st_size,
        "sha256": file_hash(path),
    }


def find_shot(plan: dict[str, Any], shot_id: str) -> dict[str, Any]:
    for shot in list_value(plan.get("shots")):
        if isinstance(shot, dict) and text_value(shot.get("shot_id")) == shot_id:
            return shot
    raise BlockedError("target_shot_not_found", f"Shot not found in video plan: {shot_id}")


def find_scene(shot: dict[str, Any], scene_id: str) -> dict[str, Any]:
    for scene in list_value(shot.get("scenes")):
        if isinstance(scene, dict) and text_value(scene.get("scene_id")) == scene_id:
            return scene
    raise BlockedError("target_scene_not_found", f"Scene not found in video plan: {scene_id}")


def storyboard_shots(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in list_value(storyboard.get("shots")) if isinstance(item, dict)]


def find_storyboard_shot(storyboard: dict[str, Any], shot_id: str) -> dict[str, Any] | None:
    return next((shot for shot in storyboard_shots(storyboard) if text_value(shot.get("shot_id")) == shot_id), None)


def find_storyboard_scene(storyboard: dict[str, Any], shot_id: str, scene_id: str) -> dict[str, Any] | None:
    shot = find_storyboard_shot(storyboard, shot_id)
    if not shot:
        return None
    return next((scene for scene in list_value(shot.get("scenes")) if isinstance(scene, dict) and text_value(scene.get("scene_id")) == scene_id), None)


def scene_dialogues(storyboard: dict[str, Any], shot_id: str, scene_id: str) -> list[dict[str, Any]]:
    scene = find_storyboard_scene(storyboard, shot_id, scene_id) or {}
    dialogues = scene.get("dialogue_items") if isinstance(scene.get("dialogue_items"), list) else scene.get("dialogues")
    return [item for item in list_value(dialogues) if isinstance(item, dict)]


def dialogue_text(dialogue: dict[str, Any]) -> str:
    return text_value(
        dialogue.get("tts_text")
        or dialogue.get("tts_dialogue")
        or dialogue.get("dialogue")
        or dialogue.get("text")
    )


def segment_dialogue_asset_keys(segment: dict[str, Any]) -> list[str]:
    values = list_value(segment.get("dialogue_asset_keys"))
    if not values:
        values = list_value(segment.get("dialogue_ids"))
    return [text_value(item) for item in values if text_value(item)]


def subtitles_for_scene(storyboard: dict[str, Any], shot: dict[str, Any], scene: dict[str, Any], scene_result: dict[str, Any]) -> list[dict[str, Any]]:
    shot_id = text_value(shot.get("shot_id"))
    scene_id = text_value(scene.get("scene_id"))
    dialogue_index = {
        text_value(item.get("dialogue_asset_key")): item
        for item in scene_dialogues(storyboard, shot_id, scene_id)
        if text_value(item.get("dialogue_asset_key"))
    }
    subtitles: list[dict[str, Any]] = []
    cursor = 0.0
    for segment_result in list_value(scene_result.get("input_segments")):
        segment = dict_value(segment_result.get("segment"))
        ids = segment_dialogue_asset_keys(segment)
        segment_duration = float(segment_result.get("duration_seconds") or 0)
        dialogue_durations = []
        for dialogue_id in ids:
            dialogue = dict_value(dialogue_index.get(dialogue_id))
            duration = float(dialogue.get("duration") or (float(dialogue.get("end") or 0) - float(dialogue.get("start") or 0)) or 0)
            dialogue_durations.append(max(0.0, duration))
        total = sum(dialogue_durations)
        if total <= 0 and ids:
            dialogue_durations = [segment_duration / len(ids) for _ in ids]
            total = segment_duration
        for dialogue_id, original_duration in zip(ids, dialogue_durations):
            dialogue = dict_value(dialogue_index.get(dialogue_id))
            text = dialogue_text(dialogue)
            if not text:
                continue
            duration = (original_duration / total * segment_duration) if total > 0 else 0
            start = cursor
            end = cursor + duration
            subtitles.append({"dialogue_id": dialogue_id, "start": round(start, 3), "end": round(end, 3), "text": text})
            cursor = end
        cursor = max(cursor, float(segment_result.get("scene_offset_seconds") or 0) + segment_duration)
    return subtitles


def srt_timestamp(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_subtitle_files(workspace: Path, key: str, subtitles: list[dict[str, Any]]) -> tuple[Path, Path]:
    output_dir = workspace / TOOL_DIR_NAME / "Output"
    srt_path = output_dir / f"{key}_Scene_Subtitles.srt"
    json_path = output_dir / f"{key}_Scene_Subtitles.json"
    lines = []
    for index, item in enumerate(subtitles, start=1):
        lines.append(str(index))
        lines.append(f"{srt_timestamp(float(item.get('start') or 0))} --> {srt_timestamp(float(item.get('end') or 0))}")
        lines.append(text_value(item.get("text")))
        lines.append("")
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("\n".join(lines), encoding="utf-8")
    write_json(json_path, {"schema_version": "analysis_v1_scene_subtitles_0.1", "items": subtitles})
    return srt_path, json_path


def ffmpeg_concat_list(workspace: Path, paths: list[Path], name: str) -> Path:
    list_path = workspace / TOOL_DIR_NAME / "Working" / f"{name}_concat.txt"
    list_path.parent.mkdir(parents=True, exist_ok=True)
    list_path.write_text("".join(f"file '{path.as_posix()}'\n" for path in paths), encoding="utf-8")
    return list_path


def even_dimension(value: int, fallback: int) -> int:
    next_value = int(value or fallback)
    if next_value < 2:
        next_value = fallback
    if next_value % 2:
        next_value -= 1
    return max(2, next_value)


def concat_canvas(input_metadata: list[dict[str, Any]]) -> tuple[int, int]:
    width = max((int(item.get("width") or 0) for item in input_metadata), default=0)
    height = max((int(item.get("height") or 0) for item in input_metadata), default=0)
    return even_dimension(width, 1080), even_dimension(height, 1920)


def parse_frame_rate(value: Any) -> float:
    text = text_value(value)
    if not text:
        return 0.0
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        den = safe_float(denominator, 0.0)
        return safe_float(numerator, 0.0) / den if den else 0.0
    return safe_float(text, 0.0)


def concat_output_fps(input_metadata: list[dict[str, Any]]) -> float:
    rates = [parse_frame_rate(item.get("avg_frame_rate") or item.get("r_frame_rate")) for item in input_metadata]
    valid = [rate for rate in rates if 1.0 <= rate <= 120.0]
    if valid and len(valid) == len(input_metadata) and max(valid) - min(valid) < 0.01:
        return round(valid[0], 3)
    return 30.0


def fps_arg(value: float) -> str:
    rounded = round(float(value or 30.0), 3)
    return str(int(rounded)) if abs(rounded - int(rounded)) < 0.001 else f"{rounded:.3f}".rstrip("0").rstrip(".")


def concat_filter(input_metadata: list[dict[str, Any]], width: int, height: int, output_fps: float) -> str:
    parts: list[str] = []
    stream_labels: list[str] = []
    fps_value = fps_arg(output_fps)
    for index, metadata in enumerate(input_metadata):
        parts.append(
            f"[{index}:v]fps={fps_value},scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v{index}]"
        )
        if metadata.get("has_audio"):
            parts.append(f"[{index}:a]aresample=48000,asetpts=PTS-STARTPTS[a{index}]")
        else:
            duration = max(0.1, float(metadata.get("duration_seconds") or 0))
            parts.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[a{index}]")
        stream_labels.append(f"[v{index}][a{index}]")
    parts.append(f"{''.join(stream_labels)}concat=n={len(input_metadata)}:v=1:a=1[v][a]")
    return ";".join(parts)


def compose_videos(workspace: Path, input_paths: list[Path], output_path: Path, scope_key: str) -> dict[str, Any]:
    if not input_paths:
        raise ToolError(f"No input videos for compose scope: {scope_key}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = ffmpeg_concat_list(workspace, input_paths, safe_name(scope_key, "compose"))
    input_metadata = [probe_media(path) for path in input_paths]
    width, height = concat_canvas(input_metadata)
    output_fps = concat_output_fps(input_metadata)
    gop = max(1, int(round(output_fps)))
    input_args = [item for path in input_paths for item in ("-i", str(path))]
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *input_args,
        "-filter_complex",
        concat_filter(input_metadata, width, height, output_fps),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-r",
        fps_arg(output_fps),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        raise ToolError(f"ffmpeg compose failed for {scope_key}: {completed.stderr[:1200]}")
    expected_duration = round(sum(float(item.get("duration_seconds") or 0) for item in input_metadata), 3)
    output_metadata = probe_media(output_path)
    actual_duration = float(output_metadata.get("duration_seconds") or 0)
    tolerance = max(1.0, expected_duration * 0.05)
    if expected_duration > 0 and actual_duration + tolerance < expected_duration:
        raise ToolError(
            f"ffmpeg compose produced short output for {scope_key}: "
            f"expected about {expected_duration:.3f}s, got {actual_duration:.3f}s"
        )
    return {
        "source": "ffmpeg_concat_filter_reencode",
        "input_count": len(input_paths),
        "concat_list": rel(workspace, list_path),
        "output_path": rel(workspace, output_path),
        "canvas": {"width": width, "height": height},
        "fps": output_fps,
        "duration_seconds": output_metadata["duration_seconds"],
        "expected_duration_seconds": expected_duration,
        "actual_duration_seconds": round(actual_duration, 3),
    }


def extract_probe_frame(video_path: Path, output_path: Path) -> bool:
    command = [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.5", "-i", str(video_path), "-frames:v", "1", str(output_path)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return completed.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0


def detect_watermark_region(workspace: Path, video_path: Path, scope_key: str, result: dict[str, Any]) -> dict[str, Any] | None:
    if Image is None:
        result.setdefault("warnings", []).append({"code": "watermark_detection_unavailable", "message": "PIL is unavailable; watermark detection skipped.", "video_path": rel(workspace, video_path)})
        return None
    frame_path = workspace / TOOL_DIR_NAME / "Working" / f"{safe_name(scope_key, 'segment')}_watermark_probe.jpg"
    if not extract_probe_frame(video_path, frame_path):
        result.setdefault("warnings", []).append({"code": "watermark_probe_frame_failed", "message": "Could not extract frame for watermark detection.", "video_path": rel(workspace, video_path)})
        return None
    try:
        image = Image.open(frame_path).convert("L")
    except Exception:
        return None
    width, height = image.size
    candidates = [
        (0, 0, int(width * 0.28), int(height * 0.16), "top_left"),
        (int(width * 0.72), 0, width, int(height * 0.16), "top_right"),
        (0, int(height * 0.84), int(width * 0.28), height, "bottom_left"),
        (int(width * 0.72), int(height * 0.84), width, height, "bottom_right"),
    ]
    best: tuple[float, tuple[int, int, int, int, str]] | None = None
    for candidate in candidates:
        x1, y1, x2, y2, _ = candidate
        crop = image.crop((x1, y1, x2, y2))
        pixels = list(crop.getdata())
        if not pixels:
            continue
        mean = sum(pixels) / len(pixels)
        variance = sum((pixel - mean) ** 2 for pixel in pixels) / len(pixels)
        score = math.sqrt(variance) / 255
        if score > WATERMARK_DETECTION_MIN_SCORE and (best is None or score > best[0]):
            best = (score, candidate)
    if not best:
        return None
    score, (x1, y1, x2, y2, label) = best
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1, "label": label, "confidence": round(min(1.0, score), 3), "strategy": "delogo"}


def remove_watermark(workspace: Path, input_path: Path, output_path: Path, region: dict[str, Any]) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    media = probe_media(input_path)
    bounded_region = clamp_watermark_region(region, int(media.get("width") or 0), int(media.get("height") or 0))
    if not bounded_region:
        raise ToolError("watermark region is outside of the video frame.")
    x = bounded_region["x"]
    y = bounded_region["y"]
    w = bounded_region["w"]
    h = bounded_region["h"]
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        f"delogo=x={x}:y={y}:w={w}:h={h}:show=0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        raise ToolError(f"watermark removal failed: {completed.stderr[:1200]}")
    return {"source": "ffmpeg_delogo", "input_path": rel(workspace, input_path), "output_path": rel(workspace, output_path), "region": bounded_region}


def clamp_watermark_region(region: dict[str, Any], frame_width: int, frame_height: int) -> dict[str, Any] | None:
    if frame_width <= 2 or frame_height <= 2:
        return None
    x = max(0, int(region.get("x") or 0))
    y = max(0, int(region.get("y") or 0))
    w = max(1, int(region.get("w") or 1))
    h = max(1, int(region.get("h") or 1))
    if x >= frame_width - 1 or y >= frame_height - 1:
        return None
    max_w = frame_width - x - 1
    max_h = frame_height - y - 1
    bounded = {
        **region,
        "x": x,
        "y": y,
        "w": min(w, max_w),
        "h": min(h, max_h),
    }
    if bounded["w"] < 2 or bounded["h"] < 2:
        return None
    return bounded


def process_watermark(workspace: Path, input_path: Path, output_path: Path, scope_key: str, args: Args, result: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    if args.watermark_mode == "never":
        return input_path, {"status": "skipped", "reason": "watermark_mode_never", "input_path": rel(workspace, input_path)}
    region = detect_watermark_region(workspace, input_path, scope_key, result)
    if not region:
        return input_path, {"status": "not_detected", "input_path": rel(workspace, input_path), "message": "No watermark detected; no processing applied."}
    confidence = safe_float(region.get("confidence"), 0.0)
    if args.watermark_mode == "auto" and confidence < WATERMARK_AUTO_MIN_CONFIDENCE:
        return input_path, {
            "status": "skipped",
            "reason": "watermark_confidence_below_auto_threshold",
            "input_path": rel(workspace, input_path),
            "region": region,
            "threshold": WATERMARK_AUTO_MIN_CONFIDENCE,
        }
    try:
        return output_path, remove_watermark(workspace, input_path, output_path, region)
    except ToolError as exc:
        result.setdefault("warnings", []).append({
            "code": "watermark_processing_skipped",
            "message": str(exc),
            "input_path": rel(workspace, input_path),
            "region": region,
        })
        return input_path, {"status": "skipped", "reason": "watermark_processing_failed", "input_path": rel(workspace, input_path), "message": str(exc), "region": region}


def hyperframes_command() -> list[str] | None:
    local_candidates = (
        Path(__file__).resolve().parent / "node_modules" / ".bin" / "hyperframes",
        REPO_ROOT / "node_modules" / ".bin" / "hyperframes",
    )
    for candidate in local_candidates:
        if candidate.exists():
            return [str(candidate)]
    found = shutil.which("hyperframes")
    if found:
        return [found]
    return None


def render_hyperframe_subtitles(workspace: Path, scene_video: Path, srt_path: Path, subtitles: list[dict[str, Any]], output_path: Path, scope_key: str, duration: float) -> dict[str, Any]:
    project_dir = workspace / TOOL_DIR_NAME / "Working" / f"HyperFrame_{safe_name(scope_key, 'scene')}"
    project_dir.mkdir(parents=True, exist_ok=True)
    media_dir = project_dir / "media"
    media_dir.mkdir(exist_ok=True)
    video_copy = media_dir / scene_video.name
    srt_copy = media_dir / srt_path.name
    shutil.copy2(scene_video, video_copy)
    shutil.copy2(srt_path, srt_copy)
    (project_dir / "gsap.min.js").write_text(
        f"""window.gsap = window.gsap || {{
  timeline() {{
    let current = 0;
    const total = {duration:.3f};
    return {{
      pause() {{
        return this;
      }},
      play() {{
        return this;
      }},
      seek(value) {{
        current = Math.max(0, Number(value) || 0);
        return this;
      }},
      totalTime(value) {{
        if (typeof value === "number") current = Math.max(0, value);
        return this;
      }},
      time() {{
        return current;
      }},
      duration() {{
        return total;
      }},
      timeScale() {{
        return this;
      }},
      from() {{
        return this;
      }},
      to() {{
        return this;
      }},
      set() {{
        return this;
      }},
    }};
  }},
}};
""",
        encoding="utf-8",
    )
    index = project_dir / "index.html"
    caption_html = []
    for index_number, item in enumerate(subtitles, start=1):
        start = float(item.get("start") or 0)
        end = max(start + 0.1, float(item.get("end") or start + 0.1))
        text = (
            text_value(item.get("text"))
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        caption_html.append(
            f'<div id="caption-{index_number}" class="clip caption" data-start="{start:.3f}" data-duration="{end - start:.3f}" data-track-index="{index_number}">{text}</div>'
        )
    index.write_text(
        f"""<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\">
  <style>
    body {{ margin: 0; background: #000; }}
    #scene {{ width: 1080px; height: 1920px; overflow: hidden; background: #000; }}
    video {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
    .caption {{
      position: absolute;
      left: 72px;
      right: 72px;
      bottom: 156px;
      z-index: 4;
      padding: 18px 24px;
      border-radius: 8px;
      background: rgba(0, 0, 0, 0.62);
      color: #fff;
      font-family: "Noto Sans Japanese", "Inter", sans-serif;
      font-size: 48px;
      line-height: 1.28;
      text-align: center;
      text-shadow: 0 2px 8px rgba(0,0,0,0.45);
      box-sizing: border-box;
    }}
  </style>
</head>
<body>
  <div id=\"scene\" data-composition-id=\"scene\" data-start=\"0\" data-width=\"1080\" data-height=\"1920\" data-duration=\"{duration:.3f}\">
    <video id=\"base\" data-start=\"0\" data-duration=\"{duration:.3f}\" data-track-index=\"0\" src=\"media/{video_copy.name}\" muted playsinline></video>
    {"".join(caption_html)}
  </div>
  <script src=\"gsap.min.js\"></script>
  <script>
    window.__timelines = window.__timelines || {{}};
    window.gsap =
      window.gsap ||
      {{
        timeline() {{
          let current = 0;
          const total = {duration:.3f};
          return {{
            pause() {{
              return this;
            }},
            play() {{
              return this;
            }},
            seek(value) {{
              current = Math.max(0, Number(value) || 0);
              return this;
            }},
            totalTime(value) {{
              if (typeof value === "number") current = Math.max(0, value);
              return this;
            }},
            time() {{
              return current;
            }},
            duration() {{
              return total;
            }},
            timeScale() {{
              return this;
            }},
            from() {{
              return this;
            }},
            to() {{
              return this;
            }},
            set() {{
              return this;
            }},
          }};
        }},
      }};
    window.__timelines.scene = window.gsap.timeline({{ paused: true }});
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command_prefix = hyperframes_command()
    if not command_prefix:
        raise ToolError("HyperFrame is not installed. Install it with: npm install --prefix ToolLibrary/Analysis_V1 --save-exact hyperframes@0.6.70")
    command = [*command_prefix, "render", "--output", str(output_path)]
    env = os.environ.copy()
    ffmpeg_dir = str(Path(ffmpeg_executable()).parent)
    env["PATH"] = f"{ffmpeg_dir}{os.pathsep}{env.get('PATH', '')}"
    try:
        completed = subprocess.run(command, cwd=project_dir, env=env, capture_output=True, text=True, check=False, timeout=HYPERFRAME_RENDER_TIMEOUT_SECONDS)
        if completed.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return {"source": "hyperframe_render", "project_dir": rel(workspace, project_dir), "srt_path": rel(workspace, srt_path), "output_path": rel(workspace, output_path)}
        raise ToolError(f"HyperFrame subtitle render failed for {scope_key}: {completed.stderr[:1200] or completed.stdout[:1200] or 'empty output'}")
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"HyperFrame subtitle render timed out for {scope_key} after {HYPERFRAME_RENDER_TIMEOUT_SECONDS}s: {text_value(exc.stderr)[:1200] or text_value(exc.stdout)[:1200]}")


def trim_render_to_base_duration(render_path: Path, base_duration: float) -> dict[str, Any]:
    render_metadata = probe_media(render_path)
    duration = max(0.1, float(base_duration or 0))
    if float(render_metadata["duration_seconds"]) <= duration + 0.02:
        return {"status": "skipped", "reason": "render_duration_within_base_duration", "render_duration_seconds": render_metadata["duration_seconds"], "base_duration_seconds": round(duration, 3)}
    trimmed_path = render_path.with_name(f"{render_path.stem}_trimmed{render_path.suffix}")
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(render_path),
        "-t",
        f"{duration:.3f}",
        "-vf",
        "fps=30,format=yuv420p",
        "-af",
        "aresample=48000:async=1:first_pts=0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(trimmed_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not trimmed_path.exists() or trimmed_path.stat().st_size <= 0:
        raise ToolError(f"ffmpeg trim failed for {render_path.name}: {completed.stderr[:1200]}")
    shutil.move(str(trimmed_path), str(render_path))
    next_metadata = probe_media(render_path)
    return {
        "status": "trimmed",
        "reason": "render_exceeded_base_duration",
        "render_duration_seconds": render_metadata["duration_seconds"],
        "base_duration_seconds": round(duration, 3),
        "output_duration_seconds": next_metadata["duration_seconds"],
    }


def record_hyperframe_warning(result: dict[str, Any], scene_result: dict[str, Any], scope_key: str, error: Exception) -> None:
    warning = {
        "code": "hyperframe_subtitle_render_failed",
        "message": f"HyperFrame subtitle render skipped for {scope_key}: {error}",
        "scene_key": scope_key,
    }
    scene_result.setdefault("warnings", []).append(warning)
    result.setdefault("warnings", []).append(warning)


def backup_before_overwrite(workspace: Path, target: Path, result: dict[str, Any]) -> None:
    if not target.exists():
        return
    batch = f"batch_{now_ms()}_06_01_overwrite_backup"
    history = workspace / ASSET_HISTORY_REL / batch
    history.mkdir(parents=True, exist_ok=True)
    backup = history / target.name
    counter = 1
    while backup.exists():
        backup = history / f"{backup.stem}_{counter}{backup.suffix}"
        counter += 1
    shutil.copy2(target, backup)
    manifest_path = history / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {
        "schema_version": "storyboard_asset_history_0.1",
        "batch_id": batch,
        "reason": "06_01_overwrite_backup",
        "created_at": now_ms(),
        "items": [],
    }
    original_rel = rel(workspace, target)
    history_rel = rel(workspace, backup)
    manifest.setdefault("items", []).append({"original_path": original_rel, "history_path": history_rel, "asset_type": "Video" if target.suffix.lower() in {".mp4", ".mov", ".webm"} else "File", "reason": "06_01_overwrite_backup", "source": "06_01"})
    manifest["updated_at"] = now_ms()
    write_json(manifest_path, manifest)
    result.setdefault("backups", []).append({"from": original_rel, "to": history_rel, "history_path": history_rel})


def publish_file(workspace: Path, source: Path, planned_rel: str, result: dict[str, Any]) -> str:
    if not source.exists() or source.stat().st_size <= 0:
        raise ToolError(f"Cannot publish missing or empty file: {source}")
    output_target = workspace / TOOL_DIR_NAME / "Output" / Path(planned_rel).name
    output_target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != output_target.resolve():
        shutil.copy2(source, output_target)
    story_target = workspace_path(workspace, planned_rel)
    story_target.parent.mkdir(parents=True, exist_ok=True)
    backup_before_overwrite(workspace, story_target, result)
    shutil.copy2(output_target, story_target)
    result.setdefault("created_files", []).extend([rel(workspace, output_target), rel(workspace, story_target)])
    return rel(workspace, story_target)


def publish_json(workspace: Path, payload: dict[str, Any], planned_rel: str, result: dict[str, Any]) -> str:
    output_target = workspace / TOOL_DIR_NAME / "Output" / Path(planned_rel).name
    write_json(output_target, payload)
    story_target = workspace_path(workspace, planned_rel)
    story_target.parent.mkdir(parents=True, exist_ok=True)
    backup_before_overwrite(workspace, story_target, result)
    write_json(story_target, payload)
    result.setdefault("created_files", []).extend([rel(workspace, output_target), rel(workspace, story_target)])
    return rel(workspace, story_target)


def scene_output_paths(shot_id: str, scene_id: str) -> dict[str, str]:
    key = scene_key(shot_id, scene_id)
    return {
        "scene_video_path": f"{STORYBOARD_WORKING_REL}/{key}_Scene_Final.mp4",
        "scene_subtitled_video_path": f"{STORYBOARD_WORKING_REL}/{key}_Scene_Subtitled_Final.mp4",
        "scene_manifest_path": f"{STORYBOARD_WORKING_REL}/{key}_SceneComposeManifest.json",
        "scene_subtitle_srt_path": f"{STORYBOARD_WORKING_REL}/{key}_Scene_Subtitles.srt",
        "scene_subtitle_json_path": f"{STORYBOARD_WORKING_REL}/{key}_Scene_Subtitles.json",
    }


def shot_output_paths(shot_id: str) -> dict[str, str]:
    key = safe_name(shot_id, "shot")
    return {
        "shot_video_path": f"{STORYBOARD_WORKING_REL}/{key}_Shot_Final.mp4",
        "shot_subtitled_video_path": f"{STORYBOARD_WORKING_REL}/{key}_Shot_Subtitled_Final.mp4",
        "shot_manifest_path": f"{STORYBOARD_WORKING_REL}/{key}_ShotComposeManifest.json",
    }


def shot_plan_output_paths() -> dict[str, str]:
    return {
        "shot_plan_video_path": f"{STORYBOARD_WORKING_REL}/ShotPlan_Final.mp4",
        "shot_plan_subtitled_video_path": f"{STORYBOARD_WORKING_REL}/ShotPlan_Subtitled_Final.mp4",
        "shot_plan_manifest_path": f"{STORYBOARD_WORKING_REL}/ShotPlanComposeManifest.json",
    }


def compose_scene(workspace: Path, args: Args, storyboard: dict[str, Any], shot: dict[str, Any], scene: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    shot_id = text_value(shot.get("shot_id"))
    scene_id = text_value(scene.get("scene_id"))
    key = scene_key(shot_id, scene_id)
    outputs = scene_output_paths(shot_id, scene_id)
    scene_result: dict[str, Any] = {"shot_id": shot_id, "scene_id": scene_id, "scene_key": key, "status": "completed", "input_segments": [], "outputs": {}, "warnings": []}
    segment_paths: list[Path] = []
    cursor = 0.0
    for segment in list_value(scene.get("segments")):
        if not isinstance(segment, dict):
            continue
        segment_id = text_value(segment.get("segment_id"))
        video_rel = text_value(dict_value(segment.get("planned_outputs")).get("video_path"))
        if not video_rel:
            raise BlockedError("segment_video_path_missing", f"Segment {segment_id} has no planned_outputs.video_path.")
        video_path = workspace_path(workspace, video_rel)
        if not video_path.exists() or video_path.stat().st_size <= 0:
            raise BlockedError("segment_video_missing", f"Segment final video is missing: {video_rel}")
        metadata = probe_media(video_path)
        watermarked_path = workspace / TOOL_DIR_NAME / "Working" / f"{key}_{safe_name(segment_id, 'segment')}_NoWatermark.mp4"
        processed_path, watermark_result = process_watermark(workspace, video_path, watermarked_path, f"{key}_{segment_id}", args, result)
        if watermark_result.get("status") == "not_detected":
            scene_result.setdefault("warnings", []).append({"code": "watermark_not_detected", "segment_id": segment_id, "video_path": video_rel})
        processed_metadata = probe_media(processed_path) if processed_path != video_path else metadata
        segment_paths.append(processed_path)
        scene_result["input_segments"].append({
            "segment_id": segment_id,
            "video_path": video_rel,
            "processed_video_path": rel(workspace, processed_path),
            "duration_seconds": processed_metadata["duration_seconds"],
            "scene_offset_seconds": round(cursor, 3),
            "metadata": metadata,
            "watermark": watermark_result,
            "segment": segment,
        })
        cursor += float(processed_metadata["duration_seconds"])

    if not segment_paths:
        raise BlockedError("scene_has_no_segments", f"Scene has no segments to compose: {shot_id}/{scene_id}")
    scene_working_video = workspace / TOOL_DIR_NAME / "Working" / f"{key}_Scene_Final.mp4"
    compose_result = compose_videos(workspace, segment_paths, scene_working_video, f"{key}_Scene")
    expected_duration = sum(float(item.get("duration_seconds") or 0) for item in scene_result["input_segments"])
    scene_video_duration = float(compose_result.get("duration_seconds") or expected_duration or 0)
    if expected_duration and scene_video_duration + 0.35 < expected_duration:
        warning = {
            "code": "scene_compose_duration_shorter_than_inputs",
            "message": f"Scene compose output is shorter than its inputs for {key}: {scene_video_duration:.3f}s < {expected_duration:.3f}s",
            "scene_key": key,
            "expected_duration_seconds": round(expected_duration, 3),
            "output_duration_seconds": round(scene_video_duration, 3),
        }
        scene_result.setdefault("warnings", []).append(warning)
        result.setdefault("warnings", []).append(warning)
    scene_video_rel = publish_file(workspace, scene_working_video, outputs["scene_video_path"], result)
    scene_result["outputs"]["scene_video_path"] = scene_video_rel
    scene_result["compose"] = compose_result

    subtitles = subtitles_for_scene(storyboard, shot, scene, scene_result)
    srt_path, subtitle_json_path = write_subtitle_files(workspace, key, subtitles)
    scene_result["outputs"]["subtitle_srt_path"] = publish_file(workspace, srt_path, outputs["scene_subtitle_srt_path"], result)
    scene_result["outputs"]["subtitle_json_path"] = publish_file(workspace, subtitle_json_path, outputs["scene_subtitle_json_path"], result)

    if args.subtitle_mode == "hyperframe":
        subtitled_working = workspace / TOOL_DIR_NAME / "Working" / f"{key}_Scene_Subtitled_Final.mp4"
        duration = max(0.1, scene_video_duration or expected_duration)
        try:
            scene_result["hyperframe"] = render_hyperframe_subtitles(workspace, scene_working_video, srt_path, subtitles, subtitled_working, key, duration)
            scene_result["hyperframe"]["duration_postprocess"] = trim_render_to_base_duration(subtitled_working, duration)
            scene_result["outputs"]["scene_subtitled_video_path"] = publish_file(workspace, subtitled_working, outputs["scene_subtitled_video_path"], result)
        except BlockedError:
            raise
        except ToolError as exc:
            record_hyperframe_warning(result, scene_result, key, exc)
            scene_result["hyperframe"] = {
                "status": "skipped",
                "reason": "hyperframe_subtitle_render_failed",
                "error": str(exc),
                "project_dir": rel(workspace, workspace / TOOL_DIR_NAME / "Working" / f"HyperFrame_{safe_name(key, 'scene')}"),
                "srt_path": rel(workspace, srt_path),
            }
            scene_result["outputs"]["scene_subtitled_video_path"] = ""
    else:
        scene_result["outputs"]["scene_subtitled_video_path"] = ""

    manifest = {
        "schema_version": "analysis_v1_scene_compose_manifest_0.1",
        "shot_id": shot_id,
        "scene_id": scene_id,
        "scene_key": key,
        "input_segments": scene_result["input_segments"],
        "outputs": scene_result["outputs"],
        "created_at": now_iso(),
    }
    scene_result["outputs"]["scene_manifest_path"] = publish_json(workspace, manifest, outputs["scene_manifest_path"], result)
    return scene_result


def compose_shot(workspace: Path, shot: dict[str, Any], scene_results: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    shot_id = text_value(shot.get("shot_id"))
    outputs = shot_output_paths(shot_id)
    shot_result: dict[str, Any] = {"shot_id": shot_id, "status": "completed", "input_scenes": [], "outputs": {}}
    for scene_result in scene_results:
        if scene_result.get("status") != "completed":
            raise BlockedError("shot_dependency_scene_blocked", f"Shot {shot_id} depends on blocked scene {scene_result.get('scene_id')}")
        subtitled = text_value(dict_value(scene_result.get("outputs")).get("scene_subtitled_video_path"))
        fallback = text_value(dict_value(scene_result.get("outputs")).get("scene_video_path"))
        selected = subtitled or fallback
        path = workspace_path(workspace, selected)
        if not path.exists():
            raise BlockedError("shot_scene_video_missing", f"Scene output missing for shot compose: {selected}")
        metadata = probe_media(path)
        shot_result["input_scenes"].append({"scene_id": scene_result.get("scene_id"), "video_path": selected, "duration_seconds": metadata["duration_seconds"], "metadata": metadata})
    input_paths = [workspace_path(workspace, item["video_path"]) for item in shot_result["input_scenes"]]
    shot_working = workspace / TOOL_DIR_NAME / "Working" / f"{safe_name(shot_id, 'shot')}_Shot_Final.mp4"
    shot_result["compose"] = compose_videos(workspace, input_paths, shot_working, f"{shot_id}_Shot")
    shot_result["outputs"]["shot_video_path"] = publish_file(workspace, shot_working, outputs["shot_video_path"], result)
    shot_result["outputs"]["shot_subtitled_video_path"] = publish_file(workspace, shot_working, outputs["shot_subtitled_video_path"], result)
    manifest = {"schema_version": "analysis_v1_shot_compose_manifest_0.1", "shot_id": shot_id, "input_scenes": shot_result["input_scenes"], "outputs": shot_result["outputs"], "created_at": now_iso()}
    shot_result["outputs"]["shot_manifest_path"] = publish_json(workspace, manifest, outputs["shot_manifest_path"], result)
    return shot_result


def compose_shot_plan(workspace: Path, shot_results: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    outputs = shot_plan_output_paths()
    shot_plan_result: dict[str, Any] = {"status": "completed", "input_shots": [], "outputs": {}}
    for shot_result in shot_results:
        if shot_result.get("status") != "completed":
            raise BlockedError("shot_plan_dependency_shot_blocked", f"ShotPlan depends on blocked shot {shot_result.get('shot_id')}")
        selected = text_value(dict_value(shot_result.get("outputs")).get("shot_subtitled_video_path") or dict_value(shot_result.get("outputs")).get("shot_video_path"))
        path = workspace_path(workspace, selected)
        if not path.exists():
            raise BlockedError("shot_plan_shot_video_missing", f"Shot output missing for ShotPlan compose: {selected}")
        metadata = probe_media(path)
        shot_plan_result["input_shots"].append({"shot_id": shot_result.get("shot_id"), "video_path": selected, "duration_seconds": metadata["duration_seconds"], "metadata": metadata})
    input_paths = [workspace_path(workspace, item["video_path"]) for item in shot_plan_result["input_shots"]]
    working = workspace / TOOL_DIR_NAME / "Working" / "ShotPlan_Final.mp4"
    shot_plan_result["compose"] = compose_videos(workspace, input_paths, working, "ShotPlan")
    shot_plan_result["outputs"]["shot_plan_video_path"] = publish_file(workspace, working, outputs["shot_plan_video_path"], result)
    shot_plan_result["outputs"]["shot_plan_subtitled_video_path"] = publish_file(workspace, working, outputs["shot_plan_subtitled_video_path"], result)
    manifest = {"schema_version": "analysis_v1_shot_plan_compose_manifest_0.1", "input_shots": shot_plan_result["input_shots"], "outputs": shot_plan_result["outputs"], "created_at": now_iso()}
    shot_plan_result["outputs"]["shot_plan_manifest_path"] = publish_json(workspace, manifest, outputs["shot_plan_manifest_path"], result)
    return shot_plan_result


def compose_assets_payload(status: str, outputs: dict[str, Any], scope: str) -> dict[str, Any]:
    payload = {
        "status": status,
        "scope": scope,
        "video_path": text_value(outputs.get(f"{scope}_video_path") or outputs.get("scene_video_path") or outputs.get("shot_video_path") or outputs.get("shot_plan_video_path")),
        "subtitled_video_path": text_value(outputs.get(f"{scope}_subtitled_video_path") or outputs.get("scene_subtitled_video_path") or outputs.get("shot_subtitled_video_path") or outputs.get("shot_plan_subtitled_video_path")),
        "manifest_path": text_value(outputs.get(f"{scope}_manifest_path") or outputs.get("scene_manifest_path") or outputs.get("shot_manifest_path") or outputs.get("shot_plan_manifest_path")),
        "updated_at": now_iso(),
        "source": TOOL_NAME,
    }
    return payload


def sync_compose_assets_to_storyboard(storyboard: dict[str, Any], scene_results: list[dict[str, Any]], shot_results: list[dict[str, Any]], shot_plan_result: dict[str, Any]) -> bool:
    changed = False
    for scene_result in scene_results:
        shot_id = text_value(scene_result.get("shot_id"))
        scene_id = text_value(scene_result.get("scene_id"))
        scene = find_storyboard_scene(storyboard, shot_id, scene_id)
        if scene is None:
            continue
        assets = scene.setdefault("compose_assets", {})
        next_payload = compose_assets_payload(text_value(scene_result.get("status")) or "completed", dict_value(scene_result.get("outputs")), "scene")
        if assets.get("scene") != next_payload:
            assets["scene"] = next_payload
            changed = True
    for shot_result in shot_results:
        shot = find_storyboard_shot(storyboard, text_value(shot_result.get("shot_id")))
        if shot is None:
            continue
        assets = shot.setdefault("compose_assets", {})
        next_payload = compose_assets_payload(text_value(shot_result.get("status")) or "completed", dict_value(shot_result.get("outputs")), "shot")
        if assets.get("shot") != next_payload:
            assets["shot"] = next_payload
            changed = True
    if shot_plan_result:
        assets = storyboard.setdefault("compose_assets", {})
        next_payload = compose_assets_payload(text_value(shot_plan_result.get("status")) or "completed", dict_value(shot_plan_result.get("outputs")), "shot_plan")
        if assets.get("shot_plan") != next_payload:
            assets["shot_plan"] = next_payload
            changed = True
    return changed


def sync_compose_assets_to_edit(workspace: Path, result: dict[str, Any]) -> bool:
    edit_path = workspace / EDIT_STORYBOARD_REL
    if not edit_path.exists():
        return False
    edit = read_json(edit_path)
    if not isinstance(edit, dict):
        return False
    changed = sync_compose_assets_to_storyboard(edit, list_value(result.get("scenes")), list_value(result.get("shots")), dict_value(result.get("shot_plan")))
    if changed:
        backup_before_overwrite(workspace, edit_path, result)
        write_json(edit_path, edit)
        result.setdefault("created_files", []).append(EDIT_STORYBOARD_REL)
    return changed


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": False,
        "requires_model_calls": False,
        "reads_session_context": [VARIABLES_REL],
        "writes_session_context": [],
        "target": {"target_type": args.target_type, "shot_id": args.shot_id, "scene_id": args.scene_id},
        "settings": {"subtitle_mode": args.subtitle_mode, "watermark_mode": args.watermark_mode},
        "created_files": [],
        "cleanup_actions": [],
        "backups": [],
        "scenes": [],
        "shots": [],
        "shot_plan": {},
        "summary": {},
        "warnings": [],
        "blocked_reasons": [],
        "force": bool(args.force),
        "resume": bool(args.resume),
        "updated_at": now_iso(),
    }


def scan_for_sensitive_output(payload: Any) -> list[dict[str, str]]:
    text = json.dumps(json_safe(payload), ensure_ascii=False).lower()
    warnings = []
    for pattern in SECRET_PATTERNS:
        if pattern in text:
            warnings.append({"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"})
    return warnings


def set_blocked(result: dict[str, Any], exc: BlockedError) -> None:
    result["status"] = "blocked"
    result.setdefault("blocked_reasons", []).append({"code": exc.code, "message": exc.message})


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_count": len(list_value(result.get("scenes"))),
        "shot_count": len(list_value(result.get("shots"))),
        "scene_completed_count": sum(1 for item in list_value(result.get("scenes")) if dict_value(item).get("status") == "completed"),
        "shot_completed_count": sum(1 for item in list_value(result.get("shots")) if dict_value(item).get("status") == "completed"),
        "shot_plan_completed": bool(dict_value(result.get("shot_plan")).get("status") == "completed"),
    }


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    try:
        if not workspace.exists() or not workspace.is_dir():
            raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
        validate_args(args)
        validate_video_execution_complete(workspace)
        if args.force:
            force_reset(workspace, result)
        ensure_tool_dirs(workspace)
        variables = load_required_json(workspace, VARIABLES_REL, "variables_missing")
        storyboard = load_required_json(workspace, STORYBOARD_REL, "storyboard_missing")
        plan = load_required_json(workspace, PLAN_REL, "plan_missing")
        source_plan_hash = plan_hash(plan)
        result["source_plan_hash"] = source_plan_hash
        copy_inputs_to_working(workspace, variables, storyboard, plan, args, result)

        selected_shots: list[dict[str, Any]]
        if args.target_type == "scene":
            shot = find_shot(plan, args.shot_id)
            scene = find_scene(shot, args.scene_id)
            scene_result = compose_scene(workspace, args, storyboard, shot, scene, result)
            result["scenes"].append(scene_result)
        elif args.target_type == "shot":
            selected_shots = [find_shot(plan, args.shot_id)]
            for shot in selected_shots:
                scene_results = []
                for scene in list_value(shot.get("scenes")):
                    if isinstance(scene, dict):
                        scene_result = compose_scene(workspace, args, storyboard, shot, scene, result)
                        scene_results.append(scene_result)
                        result["scenes"].append(scene_result)
                shot_result = compose_shot(workspace, shot, scene_results, result)
                result["shots"].append(shot_result)
        else:
            selected_shots = [shot for shot in list_value(plan.get("shots")) if isinstance(shot, dict)]
            if not selected_shots:
                raise BlockedError("plan_has_no_shots", "video_generation_plan.json has no shots[].")
            shot_results = []
            for shot in selected_shots:
                scene_results = []
                for scene in list_value(shot.get("scenes")):
                    if isinstance(scene, dict):
                        scene_result = compose_scene(workspace, args, storyboard, shot, scene, result)
                        scene_results.append(scene_result)
                        result["scenes"].append(scene_result)
                shot_result = compose_shot(workspace, shot, scene_results, result)
                shot_results.append(shot_result)
                result["shots"].append(shot_result)
            result["shot_plan"] = compose_shot_plan(workspace, shot_results, result)

        if sync_compose_assets_to_storyboard(storyboard, list_value(result.get("scenes")), list_value(result.get("shots")), dict_value(result.get("shot_plan"))):
            storyboard_path = workspace / STORYBOARD_REL
            backup_before_overwrite(workspace, storyboard_path, result)
            write_json(storyboard_path, storyboard)
            result.setdefault("created_files", []).append(STORYBOARD_REL)
        if sync_compose_assets_to_edit(workspace, result):
            result.setdefault("sync_actions", []).append({"code": "edit_storyboard_compose_assets_synced", "message": "Compose outputs were synced to koubo_storyboard_edit.json."})
        result["summary"] = summarize_result(result)
        write_json(workspace / COMPOSE_RESULT_REL, result)
        write_json(workspace / SESSION_COMPOSE_RESULT_REL, result)
        write_json(workspace / WORKING_STATE_REL, {"source_plan_hash": source_plan_hash, "target": result["target"], "summary": result["summary"], "updated_at": now_iso()})
        result.setdefault("created_files", []).extend([COMPOSE_RESULT_REL, SESSION_COMPOSE_RESULT_REL, WORKING_STATE_REL])
        warnings = scan_for_sensitive_output(result)
        if warnings:
            result["warnings"].extend(warnings)
            result["status"] = "failed"
            result.setdefault("blocked_reasons", []).append({"code": "sensitive_output_detected", "message": "Sensitive-looking content detected in output files."})
    except BlockedError as exc:
        ensure_tool_dirs(workspace) if workspace.exists() and workspace.is_dir() else None
        set_blocked(result, exc)
    except Exception as exc:
        ensure_tool_dirs(workspace) if workspace.exists() and workspace.is_dir() else None
        result["status"] = "failed"
        result.setdefault("blocked_reasons", []).append({"code": "execution_failed", "message": str(exc)})
    result["updated_at"] = now_iso()
    if workspace.exists() and workspace.is_dir():
        write_json(workspace / RESULT_REL, result)
    if args.print_json:
        print(json.dumps(json_safe(result), ensure_ascii=False, indent=2))
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Compose Analysis_V1 video plan outputs hierarchically by Scene, Shot, and Task.")
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--target-type", choices=["scene", "shot", "task"], default="task")
    parser.add_argument("--shot-id", default="")
    parser.add_argument("--scene-id", default="")
    parser.add_argument("--subtitle-mode", choices=["hyperframe", "none"], default="none")
    parser.add_argument("--watermark-mode", choices=["auto", "always", "never"], default="never")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return Args(**vars(parser.parse_args(argv)))


def main(argv: list[str]) -> int:
    if "--tool-session-root" in argv:
        try:
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge
        except ModuleNotFoundError:
            repo_root = str(Path(__file__).resolve().parents[2])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge

        framework_exit = maybe_run_framework_bridge(argv, script_path=Path(__file__), tool_name=TOOL_NAME)
        if framework_exit is not None:
            return framework_exit

    result = run(parse_args(argv))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
