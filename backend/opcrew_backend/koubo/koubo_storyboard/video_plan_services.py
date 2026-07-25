from __future__ import annotations

from typing import Any

from .video_plan_load_services import register_video_plan_load_services
from .video_plan_signature_services import register_video_plan_signature_services
from .video_plan_artifact_services import register_video_plan_artifact_services
from .video_plan_execution_state_services import register_video_plan_execution_state_services


def register_video_plan_services(ns: Any) -> None:
    register_video_plan_load_services(ns)
    register_video_plan_signature_services(ns)
    register_video_plan_artifact_services(ns)
    register_video_plan_execution_state_services(ns)
