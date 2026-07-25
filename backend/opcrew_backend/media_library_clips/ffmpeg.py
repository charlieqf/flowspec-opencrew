from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import ClipCancelled, MediaClipError
from .models import ClipProbe


REPO_ROOT = Path(__file__).resolve().parents[3]
BINARY_ENV = {
    "ffmpeg": "OPENCREW_FFMPEG_PATH",
    "ffprobe": "OPENCREW_FFPROBE_PATH",
}


def _usable_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve_media_binary(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    if name not in BINARY_ENV:
        raise ValueError("media_binary_name_invalid")
    env = environ if environ is not None else os.environ
    configured = str(env.get(BINARY_ENV[name], "") or "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            raise MediaClipError(
                "media_binary_configuration_invalid",
                f"{name} 配置路径必须是绝对路径。",
                status_code=503,
            )
        resolved = configured_path.resolve()
        if not _usable_executable(resolved):
            raise MediaClipError(
                "media_binary_configuration_invalid",
                f"{name} 配置的可执行文件不可用。",
                status_code=503,
            )
        return str(resolved)
    bundled = (repo_root / "ToolLibrary" / ".bin" / name).resolve()
    if _usable_executable(bundled):
        return str(bundled)
    located = str(which(name) or "").strip()
    if located:
        resolved = Path(located).expanduser().resolve()
        if _usable_executable(resolved):
            return str(resolved)
    raise MediaClipError(
        "media_binary_unavailable",
        f"{name} 不可用，暂时不能生成视频片段。",
        status_code=503,
    )


def inspect_media_runtime(
    *,
    ffmpeg_path: str,
    ffprobe_path: str,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    commands = {
        "ffmpeg_version": [ffmpeg_path, "-version"],
        "ffprobe_version": [ffprobe_path, "-version"],
        "encoders": [ffmpeg_path, "-hide_banner", "-encoders"],
    }
    outputs: dict[str, str] = {}
    for key, command in commands.items():
        completed = run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise MediaClipError(
                "media_binary_inspection_failed",
                "无法验证 FFmpeg 剪辑运行环境。",
                status_code=503,
            )
        outputs[key] = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    encoders = outputs["encoders"].lower()
    payload = {
        "ffmpeg_path": str(Path(ffmpeg_path).resolve()),
        "ffprobe_path": str(Path(ffprobe_path).resolve()),
        "ffmpeg_version": outputs["ffmpeg_version"].strip().splitlines()[0],
        "ffprobe_version": outputs["ffprobe_version"].strip().splitlines()[0],
        "capabilities": {
            "libx264": "libx264" in encoders,
            "aac": any(
                "aac" == token
                for line in encoders.splitlines()
                for token in line.split()[1:2]
            ),
        },
    }
    if not all(payload["capabilities"].values()):
        raise MediaClipError(
            "media_clip_codec_unavailable",
            "FFmpeg 缺少 libx264 或 AAC 编码能力。",
            status_code=503,
        )
    return payload


def milliseconds_as_seconds(value: int) -> str:
    milliseconds = int(value)
    if milliseconds < 0:
        raise ValueError("negative_media_time")
    return f"{milliseconds // 1000}.{milliseconds % 1000:03d}"


def build_ffmpeg_command(
    *,
    ffmpeg_path: str,
    source_path: Path,
    part_path: Path,
    start_ms: int,
    end_ms: int,
) -> list[str]:
    duration_ms = int(end_ms) - int(start_ms)
    if int(start_ms) < 0 or duration_ms <= 0:
        raise ValueError("clip_range_invalid")
    return [
        str(Path(ffmpeg_path).resolve()),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        milliseconds_as_seconds(int(start_ms)),
        "-accurate_seek",
        "-i",
        str(source_path.resolve()),
        "-t",
        milliseconds_as_seconds(duration_ms),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        "-y",
        str(part_path.resolve()),
    ]


def build_ffprobe_command(*, ffprobe_path: str, path: Path) -> list[str]:
    return [
        str(Path(ffprobe_path).resolve()),
        "-v",
        "error",
        "-show_entries",
        (
            "format=duration:"
            "stream=index,codec_type,codec_name,duration,"
            "avg_frame_rate,sample_rate"
        ),
        "-of",
        "json",
        str(path.resolve()),
    ]


def _positive_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _frame_rate(value: Any) -> Decimal | None:
    text = str(value or "").strip()
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        top = _positive_decimal(numerator)
        bottom = _positive_decimal(denominator)
        if top is None or bottom is None:
            return None
        return top / bottom
    return _positive_decimal(text)


def duration_tolerance_ms(
    *,
    requested_duration_ms: int,
    avg_frame_rate: Any,
    audio_codec: str | None,
    sample_rate: Any,
) -> tuple[int, int, int]:
    rate = _frame_rate(avg_frame_rate)
    video_budget = math.ceil(Decimal(1000) / rate) if rate else 50
    parsed_sample_rate = _positive_decimal(sample_rate)
    audio_budget = (
        math.ceil(Decimal(1024 * 1000) / parsed_sample_rate)
        if str(audio_codec or "").lower() == "aac"
        and parsed_sample_rate is not None
        else 0
    )
    percentage_budget = math.ceil(max(0, int(requested_duration_ms)) * 0.05)
    tolerance = min(
        250, max(video_budget, audio_budget, percentage_budget)
    )
    return tolerance, video_budget, audio_budget


def parse_ffprobe_payload(
    payload: Mapping[str, Any],
    *,
    requested_duration_ms: int,
    size_bytes: int,
) -> ClipProbe:
    if int(size_bytes) <= 0:
        raise MediaClipError(
            "media_clip_probe_failed",
            "生成的视频片段为空。",
            status_code=500,
        )
    streams = [
        item
        for item in (payload.get("streams") or [])
        if isinstance(item, Mapping)
    ]
    video = next(
        (
            item
            for item in streams
            if str(item.get("codec_type") or "") == "video"
        ),
        None,
    )
    if video is None:
        raise MediaClipError(
            "media_clip_probe_failed",
            "生成的视频片段缺少视频流。",
            status_code=500,
        )
    audio = next(
        (
            item
            for item in streams
            if str(item.get("codec_type") or "") == "audio"
        ),
        None,
    )
    durations: list[Decimal] = []
    format_payload = payload.get("format")
    if isinstance(format_payload, Mapping):
        duration = _positive_decimal(format_payload.get("duration"))
        if duration is not None:
            durations.append(duration)
    for stream in streams:
        duration = _positive_decimal(stream.get("duration"))
        if duration is not None:
            durations.append(duration)
    if not durations:
        raise MediaClipError(
            "media_clip_probe_failed",
            "无法验证生成片段的播放时长。",
            status_code=500,
        )
    actual_duration_ms = int(
        (max(durations) * Decimal(1000)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    if actual_duration_ms <= 0:
        raise MediaClipError(
            "media_clip_probe_failed",
            "生成片段的播放时长无效。",
            status_code=500,
        )
    audio_codec = (
        str(audio.get("codec_name") or "").lower() if audio is not None else None
    )
    sample_rate = audio.get("sample_rate") if audio is not None else None
    parsed_sample_rate = _positive_decimal(sample_rate)
    tolerance, video_budget, audio_budget = duration_tolerance_ms(
        requested_duration_ms=requested_duration_ms,
        avg_frame_rate=video.get("avg_frame_rate"),
        audio_codec=audio_codec,
        sample_rate=sample_rate,
    )
    if abs(actual_duration_ms - int(requested_duration_ms)) > tolerance:
        raise MediaClipError(
            "media_clip_duration_mismatch",
            "生成片段的播放时长超出允许误差。",
            status_code=500,
            details={"duration_tolerance_ms": tolerance},
        )
    return ClipProbe(
        actual_duration_ms=actual_duration_ms,
        duration_tolerance_ms=tolerance,
        video_frame_budget_ms=video_budget,
        audio_frame_budget_ms=audio_budget,
        has_audio=audio is not None,
        video_codec=str(video.get("codec_name") or ""),
        audio_codec=audio_codec,
        avg_frame_rate=str(video.get("avg_frame_rate") or ""),
        sample_rate=(
            int(parsed_sample_rate)
            if parsed_sample_rate is not None
            else None
        ),
    )


def probe_clip(
    *,
    ffprobe_path: str,
    path: Path,
    requested_duration_ms: int,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ClipProbe:
    completed = run(
        build_ffprobe_command(ffprobe_path=ffprobe_path, path=path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise MediaClipError(
            "media_clip_probe_failed",
            "生成的视频片段未通过媒体校验。",
            status_code=500,
        )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaClipError(
            "media_clip_probe_failed",
            "媒体校验返回了无效结果。",
            status_code=500,
        ) from exc
    if not isinstance(payload, Mapping):
        raise MediaClipError(
            "media_clip_probe_failed",
            "媒体校验返回了无效结果。",
            status_code=500,
        )
    return parse_ffprobe_payload(
        payload,
        requested_duration_ms=requested_duration_ms,
        size_bytes=path.stat().st_size if path.is_file() else 0,
    )


def terminate_process(process: Any, *, timeout_seconds: float = 5.0) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_seconds)


def run_ffmpeg(
    *,
    command: list[str],
    requested_duration_ms: int,
    cancel_requested: Callable[[], bool],
    on_progress: Callable[[int], None],
    on_process: Callable[[Any | None], None],
    popen: Callable[..., Any] = subprocess.Popen,
) -> None:
    process = popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        shell=False,
    )
    on_process(process)
    try:
        if cancel_requested():
            terminate_process(process)
            raise ClipCancelled()
        if process.stdout is not None:
            for raw_line in process.stdout:
                if cancel_requested():
                    terminate_process(process)
                    raise ClipCancelled()
                line = str(raw_line or "").strip()
                if not line.startswith("out_time_us="):
                    continue
                try:
                    elapsed_us = max(0, int(line.split("=", 1)[1]))
                except ValueError:
                    continue
                denominator = max(1, int(requested_duration_ms) * 1000)
                on_progress(min(99, int(elapsed_us * 99 / denominator)))
        returncode = process.wait()
        if cancel_requested():
            raise ClipCancelled()
        if returncode != 0:
            raise MediaClipError(
                "media_clip_ffmpeg_failed",
                "视频剪切失败，请检查源文件后重试。",
                status_code=500,
            )
    finally:
        for stream in (process.stdout, process.stderr):
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        on_process(None)


__all__ = [
    "BINARY_ENV",
    "REPO_ROOT",
    "build_ffmpeg_command",
    "build_ffprobe_command",
    "duration_tolerance_ms",
    "inspect_media_runtime",
    "milliseconds_as_seconds",
    "parse_ffprobe_payload",
    "probe_clip",
    "resolve_media_binary",
    "run_ffmpeg",
    "terminate_process",
]
