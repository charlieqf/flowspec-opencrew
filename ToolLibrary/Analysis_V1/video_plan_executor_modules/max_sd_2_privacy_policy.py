from __future__ import annotations

from typing import Any


MAX_SD_2_PROVIDER = "openrouter"
MAX_SD_2_MODEL = "bytedance/seedance-2.0"
RED_GRID_MODE = "red_grid_guide"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _segment(item: dict[str, Any]) -> dict[str, Any]:
    source = _dict(item.get("source_segment"))
    return source or item


def is_visible_oral_segment(item: dict[str, Any]) -> bool:
    """Return true only for the canonical Analysis_V1 visible-talking-head branch."""
    segment = _segment(item)
    tasks = _dict(segment.get("tasks"))
    if tasks.get("need_lipsync") is not True:
        return False
    if _text(tasks.get("sync_mode")).lower() != "lipsync":
        return False
    reason = _text(tasks.get("lipsync_reason")).lower()
    source = _text(tasks.get("lipsync_decision_source")).lower()
    if reason in {"user_marked_cutaway", "cutaway", "product_closeup", "no_visible_face", "no_face"}:
        return False
    if source in {"user_marked_cutaway", "product_closeup"}:
        return False
    mode = _text(segment.get("video_generation_mode") or _dict(segment.get("dance_mimic")).get("video_generation_mode")).lower()
    workflow_id = _text(segment.get("workflow_id") or _dict(segment.get("dance_mimic")).get("workflow_id")).lower()
    return workflow_id != "dance_mimic_v1" and not mode.startswith("dance_mimic")


def selected_video_is_max_sd_2(
    variables: dict[str, Any],
    provider_override: str = "",
    model_override: str = "",
) -> bool:
    selected = _dict(variables.get("default_video_config"))
    provider = _text(selected.get("provider")).lower()
    model = _text(selected.get("model")).lower()
    requested_provider = _text(provider_override).lower()
    requested_model = _text(model_override).lower()
    if requested_provider and requested_provider != provider:
        return False
    if requested_model and requested_model != model:
        return False
    return provider == MAX_SD_2_PROVIDER and model == MAX_SD_2_MODEL


def _reference_config(storyboard: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    segment = _segment(item)
    reference = _dict(segment.get("talking_head_reference")) or _dict(item.get("talking_head_reference"))
    if reference:
        return reference
    talking_head_config = _dict(storyboard.get("talking_head_config"))
    return _dict(talking_head_config.get("max_sd_2_reference"))


def should_apply_max_sd_2_oral_privacy_grid(
    variables: dict[str, Any],
    storyboard: dict[str, Any],
    item: dict[str, Any],
    provider_override: str = "",
    model_override: str = "",
) -> bool:
    """Strict gate shared by Analysis_V1 05_02, 05_06 and tail materialization.

    Every condition is required. Missing oral markers or missing privacy state fails
    closed so cutaways, product shots, other models and unrelated segments stay clean.
    """
    if not selected_video_is_max_sd_2(variables, provider_override, model_override):
        return False
    if not is_visible_oral_segment(item):
        return False
    privacy = _dict(_dict(variables.get("talking_head")).get("reference_privacy"))
    if privacy.get("enabled") is not True:
        return False
    if _text(privacy.get("reference_privacy_mode")).lower() != RED_GRID_MODE:
        return False
    if privacy.get("apply_privacy_grid_to_target_identity_image") is not True:
        return False
    reference = _reference_config(storyboard, item)
    return reference.get("privacy_grid_mode") is True and reference.get("target_identity_grid_applied") is True
