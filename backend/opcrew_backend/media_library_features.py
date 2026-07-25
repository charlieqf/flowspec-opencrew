from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict


MediaLibraryFeature = Literal[
    "analysis_runs",
    "library_search",
    "visual_semantic",
    "composite",
    "editor",
    "visual_search_v1",
    "clip_search_v1",
]

MEDIA_LIBRARY_FEATURE_FLAGS: dict[MediaLibraryFeature, str] = {
    "analysis_runs": "OPENCREW_MEDIA_ANALYSIS_RUNS_V1",
    "library_search": "OPENCREW_MEDIA_LIBRARY_SEARCH_V1",
    "visual_semantic": "OPENCREW_MEDIA_VISUAL_SEMANTIC_V1",
    "composite": "OPENCREW_MEDIA_COMPOSITE_V1",
    "editor": "OPENCREW_MEDIA_EDITOR_V1",
    "visual_search_v1": "OPENCREW_MEDIA_LIBRARY_VISUAL_SEARCH_V1",
    "clip_search_v1": "OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1",
}

_TRUE_VALUES = frozenset({"1", "true", "on", "yes"})
_FALSE_VALUES = frozenset({"0", "false", "off", "no"})


@dataclass(frozen=True)
class MediaLibraryFeatureState:
    feature: MediaLibraryFeature
    enabled: bool
    configuration_valid: bool


class MediaLibraryFeatureCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    configuration_valid: bool


class MediaLibraryCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["media_library_capabilities_v1"] = "media_library_capabilities_v1"
    features: dict[MediaLibraryFeature, MediaLibraryFeatureCapability]


def strict_bool(value: str) -> bool:
    normalized = str(value).strip().casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError("strict_boolean_invalid")


def media_library_feature_state(
    feature: MediaLibraryFeature,
) -> MediaLibraryFeatureState:
    env_name = MEDIA_LIBRARY_FEATURE_FLAGS[feature]
    raw_value = os.environ.get(env_name)
    if raw_value is None:
        return MediaLibraryFeatureState(
            feature=feature,
            enabled=True,
            configuration_valid=True,
        )
    try:
        enabled = strict_bool(raw_value)
    except ValueError:
        return MediaLibraryFeatureState(
            feature=feature,
            enabled=False,
            configuration_valid=False,
        )
    return MediaLibraryFeatureState(
        feature=feature,
        enabled=enabled,
        configuration_valid=True,
    )


def media_library_capabilities() -> MediaLibraryCapabilities:
    return MediaLibraryCapabilities(
        features={
            feature: MediaLibraryFeatureCapability(
                enabled=state.enabled,
                configuration_valid=state.configuration_valid,
            )
            for feature in MEDIA_LIBRARY_FEATURE_FLAGS
            for state in (media_library_feature_state(feature),)
        }
    )


def require_media_library_feature(
    feature: MediaLibraryFeature,
) -> None:
    state = media_library_feature_state(feature)
    if state.configuration_valid and state.enabled:
        return
    if not state.configuration_valid:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "feature_flag_invalid",
                "feature": feature,
                "user_message": "素材库功能开关配置无效，当前已安全关闭。",
                "suggested_action": "请联系管理员修正功能开关配置。",
            },
        )
    raise HTTPException(
        status_code=503,
        detail={
            "code": "feature_disabled",
            "feature": feature,
            "user_message": "该素材库功能当前未启用。",
            "suggested_action": "请联系管理员启用对应功能后重试。",
        },
    )


__all__ = [
    "MEDIA_LIBRARY_FEATURE_FLAGS",
    "MediaLibraryCapabilities",
    "MediaLibraryFeature",
    "MediaLibraryFeatureCapability",
    "MediaLibraryFeatureState",
    "media_library_capabilities",
    "media_library_feature_state",
    "require_media_library_feature",
    "strict_bool",
]
