from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
STREAM_BYTES = 1024 * 1024
DEFAULT_PREVIEW_MAX_BIT_RATE = 12_000_000
DEFAULT_PREVIEW_MAX_FPS = 30.5
DEFAULT_PREVIEW_MAX_DIMENSION = 1280
PROXY_TIMEBASE_TOLERANCE_MS = 100
LOGGER = logging.getLogger(__name__)


def proxy_timebase_guard_enabled() -> bool:
    return str(
        os.environ.get("OPENCREW_MEDIA_LIBRARY_PROXY_TIMEBASE_GUARD") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}


def proxy_timebase_guard_result(
    source_duration_ms: Any,
    preview_duration_ms: Any,
) -> dict[str, Any]:
    try:
        source_ms = int(source_duration_ms or 0)
        preview_ms = int(preview_duration_ms or 0)
    except (TypeError, ValueError):
        source_ms = 0
        preview_ms = 0
    delta_ms = abs(source_ms - preview_ms) if source_ms and preview_ms else None
    if not source_ms:
        return {
            "valid": False,
            "reason": "source_duration_unavailable",
            "source_duration_ms": source_ms or None,
            "preview_duration_ms": preview_ms or None,
            "delta_ms": delta_ms,
        }
    if not preview_ms:
        return {
            "valid": False,
            "reason": "preview_duration_unavailable",
            "source_duration_ms": source_ms,
            "preview_duration_ms": preview_ms or None,
            "delta_ms": delta_ms,
        }
    return {
        "valid": delta_ms <= PROXY_TIMEBASE_TOLERANCE_MS,
        "reason": (
            "within_tolerance"
            if delta_ms <= PROXY_TIMEBASE_TOLERANCE_MS
            else "duration_delta_exceeded"
        ),
        "source_duration_ms": source_ms,
        "preview_duration_ms": preview_ms,
        "delta_ms": delta_ms,
    }


def safe_video_filename(filename: str) -> str:
    raw = Path(str(filename or "").replace("\\", "/")).name.strip()
    if not raw or raw in {".", ".."}:
        raise HTTPException(status_code=422, detail={"code": "media_upload_filename_invalid", "message": "视频文件名无效。"})
    suffix = Path(raw).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise HTTPException(status_code=415, detail={"code": "media_upload_type_unsupported", "message": "仅支持 MP4、MOV、M4V 和 WebM 视频。"})
    stem = Path(raw).stem.strip() or "video"
    safe_stem = re.sub(r"[\x00-\x1f/:*?\"<>|]", "_", stem).strip(" .") or "video"
    return f"{safe_stem[:220]}{suffix}"


def display_name_from_filename(filename: str) -> str:
    return Path(filename).stem.strip() or "未命名素材"


def upload_chunk_dir(workspace: Path, upload_id: str) -> Path:
    return workspace / ".media_uploads" / upload_id


def chunk_path(workspace: Path, upload_id: str, chunk_index: int) -> Path:
    return upload_chunk_dir(workspace, upload_id) / f"chunk_{chunk_index:06d}.part"


async def write_chunk(workspace: Path, upload_id: str, chunk_index: int, upload: UploadFile, *, expected_bytes: int) -> int:
    target = chunk_path(workspace, upload_id, chunk_index)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{time.time_ns()}.upload")
    written = 0
    try:
        with temporary.open("wb") as handle:
            while True:
                data = await upload.read(STREAM_BYTES)
                if not data:
                    break
                written += len(data)
                if written > expected_bytes:
                    raise HTTPException(status_code=413, detail={"code": "media_upload_chunk_too_large", "message": "上传分片超过预期大小。"})
                handle.write(data)
        if written != expected_bytes:
            raise HTTPException(status_code=422, detail={"code": "media_upload_chunk_size_mismatch", "message": f"上传分片大小不正确，应为 {expected_bytes} 字节，实际为 {written} 字节。"})
        temporary.replace(target)
        return written
    finally:
        temporary.unlink(missing_ok=True)


def merge_chunks(
    workspace: Path,
    upload_id: str,
    safe_filename: str,
    *,
    total_chunks: int,
    expected_size: int,
    finalization_token: str = "",
) -> tuple[str, Path, str]:
    source_dir = upload_chunk_dir(workspace, upload_id)
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    final_path = inbox / safe_filename
    source_rel = f"inbox/{safe_filename}"
    # A worker may die after the atomic final rename but before publishing the
    # ready DB state. In that case the chunks may already be gone; reuse the
    # fully sized final file when a stale finalization claim is recovered.
    if final_path.is_file() and final_path.stat().st_size == expected_size:
        shutil.rmtree(source_dir, ignore_errors=True)
        return source_rel, final_path, sha256_file(final_path)
    missing = [index for index in range(total_chunks) if not chunk_path(workspace, upload_id, index).is_file()]
    if missing:
        raise HTTPException(status_code=409, detail={"code": "media_upload_chunks_missing", "message": f"缺少 {len(missing)} 个上传分片。", "missing_chunks": missing[:50]})
    token_suffix = re.sub(r"[^a-zA-Z0-9_-]", "", finalization_token)[:64]
    temporary = inbox / f".{safe_filename}.{upload_id}.{token_suffix or 'finalizing'}.part"
    merged = 0
    digest = hashlib.sha256()
    try:
        with temporary.open("wb") as output:
            for index in range(total_chunks):
                with chunk_path(workspace, upload_id, index).open("rb") as source:
                    while True:
                        data = source.read(STREAM_BYTES)
                        if not data:
                            break
                        merged += len(data)
                        if merged > expected_size:
                            raise HTTPException(status_code=422, detail={"code": "media_upload_size_mismatch", "message": "合并后的视频大于声明大小。"})
                        digest.update(data)
                        output.write(data)
        if merged != expected_size:
            raise HTTPException(status_code=422, detail={"code": "media_upload_size_mismatch", "message": f"合并后的视频大小不正确，应为 {expected_size} 字节，实际为 {merged} 字节。"})
        temporary.replace(final_path)
    finally:
        temporary.unlink(missing_ok=True)
    shutil.rmtree(source_dir, ignore_errors=True)
    parent = source_dir.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    return source_rel, final_path, digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            data = handle.read(STREAM_BYTES)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def remove_upload_files(workspace: Path, upload_id: str, safe_filename: str = "") -> None:
    shutil.rmtree(upload_chunk_dir(workspace, upload_id), ignore_errors=True)
    upload_root = workspace / ".media_uploads"
    if upload_root.exists() and not any(upload_root.iterdir()):
        upload_root.rmdir()
    if safe_filename:
        (workspace / "inbox" / safe_filename).unlink(missing_ok=True)


def _ffprobe_binary() -> str:
    configured = str(os.environ.get("OPENCREW_FFPROBE_PATH") or "").strip()
    if configured and Path(configured).is_file():
        return configured
    repo_binary = Path(__file__).resolve().parents[3] / "ToolLibrary" / ".bin" / "ffprobe"
    if repo_binary.is_file():
        return str(repo_binary)
    return shutil.which("ffprobe") or ""


def _ffmpeg_binary() -> str:
    configured = str(os.environ.get("OPENCREW_FFMPEG_PATH") or "").strip()
    if configured and Path(configured).is_file():
        return configured
    repo_binary = Path(__file__).resolve().parents[3] / "ToolLibrary" / ".bin" / "ffmpeg"
    if repo_binary.is_file():
        return str(repo_binary)
    return shutil.which("ffmpeg") or ""


def _fraction_value(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        if "/" in raw:
            numerator, denominator = raw.split("/", 1)
            return float(numerator) / float(denominator) if float(denominator) else 0.0
        return float(raw)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _configured_preview_max_bit_rate() -> int:
    try:
        return max(1_000_000, int(os.environ.get("OPENCREW_MEDIA_LIBRARY_PREVIEW_MAX_BIT_RATE") or DEFAULT_PREVIEW_MAX_BIT_RATE))
    except ValueError:
        return DEFAULT_PREVIEW_MAX_BIT_RATE


def should_create_proxy_preview(path: Path, metadata: dict[str, Any]) -> bool:
    """Return whether the source is too expensive or incompatible for direct browser playback."""

    duration_ms = max(0, int(metadata.get("duration_ms") or 0))
    measured_bit_rate = max(0, int(metadata.get("bit_rate") or 0), int(metadata.get("video_bit_rate") or 0))
    if not measured_bit_rate and duration_ms and path.is_file():
        measured_bit_rate = round(path.stat().st_size * 8 * 1000 / duration_ms)
    fps = max(0.0, float(metadata.get("fps") or 0.0))
    width = max(0, int(metadata.get("width") or 0))
    height = max(0, int(metadata.get("height") or 0))
    codec_name = str(metadata.get("codec_name") or "").lower()
    pixel_format = str(metadata.get("pixel_format") or "").lower()
    suffix = path.suffix.lower()
    return any(
        (
            measured_bit_rate > _configured_preview_max_bit_rate(),
            fps > DEFAULT_PREVIEW_MAX_FPS,
            max(width, height) > 1920,
            codec_name not in {"", "h264"},
            pixel_format not in {"", "yuv420p", "yuvj420p"},
            suffix not in {".mp4", ".mov", ".m4v"},
        )
    )


def create_proxy_preview(
    workspace: Path,
    asset_id: str,
    source: Path,
    metadata: dict[str, Any],
) -> tuple[str, Path] | None:
    """Create a bounded-bandwidth, fast-start MP4 while preserving the original source."""

    if not should_create_proxy_preview(source, metadata):
        return None
    ffmpeg = _ffmpeg_binary()
    if not ffmpeg:
        raise RuntimeError("该视频需要生成浏览器预览，但未找到 FFmpeg。")
    safe_asset_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(asset_id or "asset"))[:160] or "asset"
    preview_dir = workspace / "SessionOutput" / "media_library" / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    final_path = preview_dir / f"{safe_asset_id}.mp4"
    temporary = preview_dir / f".{safe_asset_id}.{os.getpid()}.{time.time_ns()}.mp4"
    duration_seconds = max(0.0, float(metadata.get("duration_ms") or 0) / 1000.0)
    timeout_seconds = max(120, min(7200, round(duration_seconds * 5) or 120))
    scale_filter = (
        f"scale={DEFAULT_PREVIEW_MAX_DIMENSION}:{DEFAULT_PREVIEW_MAX_DIMENSION}:"
        "force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30"
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-sn",
        "-dn",
        "-vf",
        scale_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-maxrate",
        "5000k",
        "-bufsize",
        "10000k",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(temporary),
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout_seconds)
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
            detail = (result.stderr or result.stdout or "FFmpeg 未生成预览文件").strip()[-1200:]
            raise RuntimeError(f"生成浏览器预览失败：{detail}")
        if proxy_timebase_guard_enabled():
            preview_metadata = probe_video(temporary)
            guard = proxy_timebase_guard_result(
                metadata.get("duration_ms"),
                preview_metadata.get("duration_ms"),
            )
            if not guard["valid"]:
                LOGGER.warning(
                    "media_library_proxy_timebase_guard_rejected "
                    "asset_id=%s reason=%s source_duration_ms=%s "
                    "preview_duration_ms=%s delta_ms=%s",
                    safe_asset_id,
                    guard["reason"],
                    guard["source_duration_ms"],
                    guard["preview_duration_ms"],
                    guard["delta_ms"],
                )
                return None
        temporary.replace(final_path)
    finally:
        temporary.unlink(missing_ok=True)
    return f"SessionOutput/media_library/previews/{final_path.name}", final_path


def probe_video(path: Path) -> dict[str, Any]:
    ffprobe = _ffprobe_binary()
    if not ffprobe:
        return {}
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,codec_name,pix_fmt,bit_rate,avg_frame_rate,r_frame_rate:format=duration,bit_rate",
                "-of",
                "json",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {}
        payload = json.loads(result.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        format_payload = payload.get("format") or {}
        duration = float(format_payload.get("duration") or 0)
        fps = _fraction_value(stream.get("avg_frame_rate")) or _fraction_value(stream.get("r_frame_rate"))
        return {
            "duration_ms": max(0, round(duration * 1000)) or None,
            "width": int(stream.get("width") or 0) or None,
            "height": int(stream.get("height") or 0) or None,
            "codec_name": str(stream.get("codec_name") or "") or None,
            "pixel_format": str(stream.get("pix_fmt") or "") or None,
            "video_bit_rate": int(stream.get("bit_rate") or 0) or None,
            "bit_rate": int(format_payload.get("bit_rate") or 0) or None,
            "fps": fps or None,
        }
    except Exception:
        return {}
