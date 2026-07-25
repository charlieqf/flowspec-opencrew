from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from pathlib import Path

from fastapi import HTTPException


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
THUMBNAIL_MAX_SIDE = 360
THUMBNAIL_JPEG_QUALITY = 78
THUMBNAIL_GENERATION_CONCURRENCY = 3
_thumbnail_generation_semaphore = threading.Semaphore(THUMBNAIL_GENERATION_CONCURRENCY)


def media_thumbnail_supported(relative_path: str) -> bool:
    return Path(relative_path).suffix.lower() in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def _media_kind(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    raise HTTPException(status_code=415, detail="Thumbnail is not supported for this file type")


def _load_cv2() -> object:
    try:
        import cv2  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Thumbnail generation is unavailable") from exc
    return cv2


def _cache_path(root: Path, relative_path: str, source: Path, *, max_side: int) -> Path:
    stat = source.stat()
    key = hashlib.sha256(
        json.dumps(
            {
                "path": relative_path,
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
                "max_side": max_side,
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:32]
    return root / "meta" / "thumbnails" / f"{key}.jpg"


def _resize_frame(cv2: object, frame: object, max_side: int) -> object:
    height, width = frame.shape[:2]
    largest = max(width, height)
    if largest <= 0:
        raise HTTPException(status_code=415, detail="Invalid media dimensions")
    if largest <= max_side:
        return frame
    scale = max_side / largest
    next_width = max(1, int(round(width * scale)))
    next_height = max(1, int(round(height * scale)))
    return cv2.resize(frame, (next_width, next_height), interpolation=cv2.INTER_AREA)


def _write_jpeg(cv2: object, frame: object, target: Path) -> None:
    try:
        ok = cv2.imwrite(str(target), frame, [int(cv2.IMWRITE_JPEG_QUALITY), THUMBNAIL_JPEG_QUALITY])
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to write thumbnail") from exc
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to write thumbnail")


def _write_image_thumbnail(cv2: object, source: Path, target: Path, *, max_side: int) -> None:
    frame = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=415, detail="Unable to read image for thumbnail")
    _write_jpeg(cv2, _resize_frame(cv2, frame, max_side), target)


def _read_video_frame(cv2: object, source: Path) -> object | None:
    capture = cv2.VideoCapture(str(source))
    try:
        if not capture.isOpened():
            return None
        for offset_ms in (500, 1000, 0):
            capture.set(cv2.CAP_PROP_POS_MSEC, float(offset_ms))
            ok, frame = capture.read()
            if ok and frame is not None:
                return frame
        return None
    finally:
        capture.release()


def _write_video_thumbnail(cv2: object, source: Path, target: Path, *, max_side: int) -> None:
    frame = _read_video_frame(cv2, source)
    if frame is None:
        raise HTTPException(status_code=415, detail="Unable to read video frame for thumbnail")
    _write_jpeg(cv2, _resize_frame(cv2, frame, max_side), target)


def ensure_media_thumbnail(root: Path, relative_path: str, source: Path, *, max_side: int = THUMBNAIL_MAX_SIDE) -> Path:
    kind = _media_kind(relative_path)
    target = _cache_path(root, relative_path, source, max_side=max_side)
    if target.exists() and target.is_file():
        return target

    with _thumbnail_generation_semaphore:
        if target.exists() and target.is_file():
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.stem}.{os.getpid()}.{secrets.token_hex(6)}.jpg")
        cv2 = _load_cv2()
        try:
            if kind == "image":
                _write_image_thumbnail(cv2, source, tmp, max_side=max_side)
            else:
                _write_video_thumbnail(cv2, source, tmp, max_side=max_side)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
    return target
