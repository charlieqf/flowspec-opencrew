from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from io import BytesIO
from pathlib import Path


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
AUDIO_EXTS = {".wav", ".m4a", ".mp3", ".aac", ".ogg", ".oga", ".flac", ".opus", ".aiff", ".aif", ".caf", ".weba", ".wma"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS


class MediaSanitizeError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def media_binary_candidates(name: str) -> list[Path]:
    root = repo_root()
    return [
        root / ".bin" / name,
        root / "ToolLibrary" / ".bin" / name,
        root / "ToolLibrary" / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
        root / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
        root / "backend" / ".venv" / "bin" / name,
        root / "backend" / ".venv" / "bin" / f"static_{name}",
    ]


def media_binary(name: str) -> str:
    env_value = os.environ.get(f"OPENCREW_{name.upper()}_PATH", "").strip()
    if env_value and Path(env_value).exists():
        return env_value
    found = shutil.which(name)
    if found:
        return found
    for candidate in media_binary_candidates(name):
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return ""
    return ""


def ffmpeg_binary() -> str:
    binary = media_binary("ffmpeg")
    if not binary:
        raise MediaSanitizeError("ffmpeg is unavailable for media metadata sanitization")
    return binary


def _temporary_sibling(path: Path) -> Path:
    return path.with_name(f".{path.name}.sanitize-{uuid.uuid4().hex}.tmp{path.suffix}")


def _replace_with_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temporary_sibling(path)
    try:
        tmp.write_bytes(content)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _image_format_for_suffix(suffix: str) -> str:
    normalized = suffix.lower()
    if normalized in {".jpg", ".jpeg"}:
        return "JPEG"
    if normalized == ".webp":
        return "WEBP"
    return "PNG"


def sanitize_image_bytes(content: bytes, suffix: str = ".png") -> bytes:
    if not content:
        raise MediaSanitizeError("image content is empty")
    try:
        from PIL import Image
    except Exception as exc:
        raise MediaSanitizeError(f"Pillow is unavailable for image metadata sanitization: {exc}") from exc

    output_format = _image_format_for_suffix(suffix)
    try:
        with Image.open(BytesIO(content)) as image:
            icc_profile = image.info.get("icc_profile")
            frame = image.copy()
    except Exception as exc:
        raise MediaSanitizeError(f"image metadata sanitization could not parse image: {exc}") from exc

    save_kwargs: dict[str, object] = {}
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    if output_format == "JPEG":
        if frame.mode not in {"RGB", "L"}:
            frame = frame.convert("RGB")
        save_kwargs.update({"quality": 95, "optimize": True})
    elif output_format == "PNG":
        save_kwargs["optimize"] = True
    elif output_format == "WEBP":
        if frame.mode not in {"RGB", "RGBA"}:
            frame = frame.convert("RGBA" if "A" in frame.getbands() else "RGB")
        save_kwargs.update({"quality": 95, "method": 6})

    output = BytesIO()
    try:
        frame.save(output, output_format, **save_kwargs)
    except Exception as exc:
        raise MediaSanitizeError(f"image metadata sanitization failed: {exc}") from exc
    finally:
        frame.close()
    return output.getvalue()


def write_sanitized_image_bytes(path: Path, content: bytes) -> None:
    _replace_with_bytes(path, sanitize_image_bytes(content, path.suffix or ".png"))


def sanitize_image_file(source: Path, target: Path | None = None) -> None:
    destination = target or source
    write_sanitized_image_bytes(destination, source.read_bytes())


def media_kind_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTS:
        return "image"
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in AUDIO_EXTS:
        return "audio"
    return ""


def _run_sanitize_command(path: Path, tmp: Path, extra_args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    command = [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-map_metadata",
        "-1",
        "-map_metadata:s",
        "-1",
        "-map_chapters",
        "-1",
        *extra_args,
        str(tmp),
    ]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def sanitize_media_file_metadata(path: Path, *, kind: str, extra_args: list[str] | None = None, timeout: int = 120) -> None:
    if not path.exists() or not path.is_file():
        raise MediaSanitizeError(f"{kind} output file is missing")
    if path.stat().st_size <= 0:
        raise MediaSanitizeError(f"{kind} output file is empty")
    tmp = _temporary_sibling(path)
    try:
        completed = _run_sanitize_command(path, tmp, extra_args or ["-c", "copy"], timeout=timeout)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise MediaSanitizeError(f"{kind} metadata sanitization failed to run ffmpeg: {exc}") from exc
    if completed.returncode != 0:
        tmp.unlink(missing_ok=True)
        detail = (completed.stderr or completed.stdout or "").strip()[:2000]
        raise MediaSanitizeError(f"{kind} metadata sanitization failed: {detail or 'ffmpeg returned an error'}")
    try:
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def sanitize_video_file_metadata(path: Path) -> None:
    sanitize_media_file_metadata(path, kind="video")


def sanitize_audio_file_metadata(path: Path) -> None:
    try:
        sanitize_media_file_metadata(path, kind="audio")
    except MediaSanitizeError as first_error:
        try:
            sanitize_media_file_metadata(path, kind="audio", extra_args=["-vn"], timeout=180)
        except MediaSanitizeError as second_error:
            raise MediaSanitizeError(f"{first_error}; audio transcode fallback failed: {second_error}") from second_error


def sanitize_file_in_place(path: Path) -> None:
    kind = media_kind_for_path(path)
    if kind == "image":
        sanitize_image_file(path)
        return
    if kind == "video":
        sanitize_video_file_metadata(path)
        return
    if kind == "audio":
        sanitize_audio_file_metadata(path)
        return
    raise MediaSanitizeError(f"unsupported media type for metadata sanitization: {path.suffix or '<none>'}")
