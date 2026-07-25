from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - exercised through the caller's dependency guard
    Image = None
    ImageOps = None


VIDEO_ASPECT_RATIOS = {"9:16": 9 / 16, "16:9": 16 / 9}
VIDEO_PROMPT_FIELDS = {
    "storyboard_prompt",
    "context_prompt",
    "positive_prompt",
    "negative_prompt",
    "visual_prompt",
    "prompt",
}


class VideoAspectError(RuntimeError):
    pass


def normalize_video_aspect(value: Any, fallback: str = "9:16") -> str:
    aspect = str(value or "").strip()
    return aspect if aspect in VIDEO_ASPECT_RATIOS else fallback


def video_aspect_for_dimensions(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        raise VideoAspectError(f"Invalid image dimensions: {width}x{height}")
    return "16:9" if width > height else "9:16"


def image_dimensions_and_aspect(path: Path) -> tuple[int, int, str]:
    if Image is None:
        raise VideoAspectError("Pillow is required to inspect StoryBoard video first frames.")
    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened) if ImageOps is not None else opened
            width, height = image.size
    except Exception as exc:
        raise VideoAspectError(f"Invalid StoryBoard video first frame: {path}: {exc}") from exc
    return int(width), int(height), video_aspect_for_dimensions(int(width), int(height))


def _center_crop_box(width: int, height: int, aspect: str) -> tuple[int, int, int, int]:
    target_ratio = VIDEO_ASPECT_RATIOS[normalize_video_aspect(aspect)]
    source_ratio = width / height
    if abs(source_ratio - target_ratio) <= 0.002:
        return 0, 0, width, height
    if source_ratio > target_ratio:
        crop_width = max(1, min(width, int(round(height * target_ratio))))
        left = max(0, (width - crop_width) // 2)
        return left, 0, left + crop_width, height
    crop_height = max(1, min(height, int(round(width / target_ratio))))
    top = max(0, (height - crop_height) // 2)
    return 0, top, width, top + crop_height


def normalize_video_first_frame(image_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    """EXIF-normalize and center-crop a provider input without stretching it."""
    if Image is None:
        raise VideoAspectError("Pillow is required to crop StoryBoard video first frames.")
    target = output_path or image_path
    try:
        with Image.open(image_path) as opened:
            source_mode = opened.mode
            image = ImageOps.exif_transpose(opened) if ImageOps is not None else opened.copy()
            image = image.copy()
            original_width, original_height = image.size
            aspect = video_aspect_for_dimensions(original_width, original_height)
            crop_box = _center_crop_box(original_width, original_height, aspect)
            if crop_box != (0, 0, original_width, original_height):
                image = image.crop(crop_box)
            target_is_jpeg = target.suffix.lower() in {".jpg", ".jpeg"}
            if target_is_jpeg and image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            target.parent.mkdir(parents=True, exist_ok=True)
            save_kwargs: dict[str, Any] = {}
            if target_is_jpeg:
                save_kwargs = {"quality": 95, "subsampling": 0}
            image.save(target, **save_kwargs)
            final_width, final_height = image.size
    except Exception as exc:
        if isinstance(exc, VideoAspectError):
            raise
        raise VideoAspectError(f"Unable to normalize StoryBoard video first frame: {image_path}: {exc}") from exc
    return {
        "source_path": str(image_path),
        "output_path": str(target),
        "aspect": aspect,
        "orientation": "landscape" if aspect == "16:9" else "portrait",
        "original_width": int(original_width),
        "original_height": int(original_height),
        "original_mode": source_mode,
        "crop_box": list(crop_box),
        "final_width": int(final_width),
        "final_height": int(final_height),
        "cropped": crop_box != (0, 0, original_width, original_height),
        "resized": False,
    }


def provider_config_for_video_aspect(config: dict[str, Any], aspect: str) -> dict[str, Any]:
    normalized = normalize_video_aspect(aspect)
    return {
        **config,
        "aspect_ratio": normalized,
        "default_aspect_ratio": normalized,
        "ratio": normalized,
        "default_ratio": normalized,
        "requested_aspect": normalized,
    }


def _rewrite_prompt_text(value: str, aspect: str) -> str:
    normalized = normalize_video_aspect(aspect)
    if normalized == "16:9":
        value = re.sub(r"\bvertical\s+9\s*:\s*16\b", "horizontal 16:9", value, flags=re.IGNORECASE)
        value = re.sub(r"\bportrait\s+9\s*:\s*16\b", "landscape 16:9", value, flags=re.IGNORECASE)
        value = re.sub(r"竖屏\s*9\s*:\s*16", "横屏 16:9", value)
        return re.sub(r"(?<!\d)9\s*:\s*16(?!\d)", "16:9", value)
    value = re.sub(r"\bhorizontal\s+16\s*:\s*9\b", "vertical 9:16", value, flags=re.IGNORECASE)
    value = re.sub(r"\blandscape\s+16\s*:\s*9\b", "portrait 9:16", value, flags=re.IGNORECASE)
    value = re.sub(r"横屏\s*16\s*:\s*9", "竖屏 9:16", value)
    return re.sub(r"(?<!\d)16\s*:\s*9(?!\d)", "9:16", value)


def prompt_package_for_video_aspect(package: dict[str, Any], aspect: str) -> dict[str, Any]:
    normalized = normalize_video_aspect(aspect)
    result = copy.deepcopy(package)
    for key in VIDEO_PROMPT_FIELDS:
        value = result.get(key)
        if isinstance(value, str):
            result[key] = _rewrite_prompt_text(value, normalized)
    extracted = result.get("extracted_fields") if isinstance(result.get("extracted_fields"), dict) else {}
    result["extracted_fields"] = {**extracted, "aspect_ratio": normalized}
    result["requested_aspect"] = normalized
    return result


def rewrite_video_prompt_file(path: Path, aspect: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise VideoAspectError(f"Unable to read StoryBoard video prompt: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise VideoAspectError(f"StoryBoard video prompt must contain an object: {path}")
    rewritten = prompt_package_for_video_aspect(payload, aspect)
    path.write_text(json.dumps(rewritten, ensure_ascii=False, indent=2), encoding="utf-8")
    return rewritten
