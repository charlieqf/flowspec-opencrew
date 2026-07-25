from __future__ import annotations

from typing import Any


DEFAULT_TALKING_HEAD_VIDEO_MODEL_KEY = "max_1_5_x"
DEFAULT_TALKING_HEAD_PRIVACY_GRID_PRESET = "dense_12_1"

TALKING_HEAD_PRIVACY_GRID_PRESETS: dict[str, dict[str, Any]] = {
    "dense_12_1": {
        "label": "密集 12×1（默认）",
        "cell_size_reference": 12,
        "line_width_reference": 1.0,
    },
    "dense_12_0_5": {
        "label": "密集细线 12×0.5",
        "cell_size_reference": 12,
        "line_width_reference": 0.5,
    },
    "medium_dense_24_1": {
        "label": "较密 24×1",
        "cell_size_reference": 24,
        "line_width_reference": 1.0,
    },
    "medium_dense_24_0_5": {
        "label": "较密细线 24×0.5",
        "cell_size_reference": 24,
        "line_width_reference": 0.5,
    },
    "sparse_36_1": {
        "label": "稀疏 36×1",
        "cell_size_reference": 36,
        "line_width_reference": 1.0,
    },
    "sparse_36_0_5": {
        "label": "稀疏细线 36×0.5",
        "cell_size_reference": 36,
        "line_width_reference": 0.5,
    },
    "very_sparse_48_1": {
        "label": "极疏 48×1",
        "cell_size_reference": 48,
        "line_width_reference": 1.0,
    },
    "very_sparse_48_0_5": {
        "label": "极疏细线 48×0.5",
        "cell_size_reference": 48,
        "line_width_reference": 0.5,
    },
}

TALKING_HEAD_VIDEO_MODELS: dict[str, dict[str, Any]] = {
    "flush_x": {
        "provider": "xai",
        "model": "grok-imagine-video",
        "model_alias": "Flush X",
        "max_duration_seconds": 15,
    },
    "max_1_5_x": {
        "provider": "xai",
        "model": "grok-imagine-video-1.5-preview",
        "model_alias": "Max 1.5 X",
        "max_duration_seconds": 15,
    },
    "max_2_7_w": {
        "provider": "wan",
        "model": "wan2.7-r2v",
        "model_alias": "Max 2.7 W",
        "max_duration_seconds": 10,
    },
    "max_sd_2": {
        "provider": "openrouter",
        "model": "bytedance/seedance-2.0",
        "model_alias": "Max SD 2",
        "max_duration_seconds": 15,
    },
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def talking_head_video_model_key(value: Any) -> str:
    source = value if isinstance(value, dict) else {}
    requested_key = _text(source.get("model_key") or source.get("key")).lower()
    if requested_key in TALKING_HEAD_VIDEO_MODELS:
        return requested_key

    provider = _text(source.get("provider")).lower()
    model = _text(source.get("model"))
    alias = _text(source.get("model_alias") or source.get("alias")).lower()
    for key, item in TALKING_HEAD_VIDEO_MODELS.items():
        if provider and model and provider == item["provider"] and model == item["model"]:
            return key
        if alias and alias == str(item["model_alias"]).lower():
            return key
    return ""


def resolve_talking_head_video_model(
    *,
    model_key: Any = "",
    provider: Any = "",
    model: Any = "",
    model_alias: Any = "",
    default_key: str = "",
) -> dict[str, Any] | None:
    key = talking_head_video_model_key({
        "model_key": model_key,
        "provider": provider,
        "model": model,
        "model_alias": model_alias,
    })
    if not key and default_key in TALKING_HEAD_VIDEO_MODELS:
        key = default_key
    if not key:
        return None
    return {
        "model_key": key,
        **TALKING_HEAD_VIDEO_MODELS[key],
        "executor": "analysis_v1_video_plan_executor",
    }


def resolve_talking_head_privacy_grid_preset(value: Any) -> dict[str, Any] | None:
    key = _text(value).lower()
    if key not in TALKING_HEAD_PRIVACY_GRID_PRESETS:
        return None
    return {
        "preset": key,
        **TALKING_HEAD_PRIVACY_GRID_PRESETS[key],
    }
