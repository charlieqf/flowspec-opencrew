from __future__ import annotations

from collections.abc import Mapping
from typing import Any


ACTIVE_ANALYSIS_STATUSES = frozenset({"queued", "running"})
TASK_ANALYSIS_STATUS_FIELDS = (
    "dialogue_status",
    "visual_structure_status",
    "visual_semantic_status",
    "composite_status",
)


def derive_visual_status(structure: str, semantic: str) -> str:
    if structure in ACTIVE_ANALYSIS_STATUSES:
        return "running"
    if structure == "stale":
        return "stale"
    if structure == "blocked":
        return "blocked"
    if structure == "ready" and semantic == "blocked":
        return "blocked"
    if structure == "ready" and semantic in ACTIVE_ANALYSIS_STATUSES:
        return "running"
    if structure == "ready" and semantic == "ready":
        return "ready"
    if structure == "ready" and semantic == "stale":
        return "stale"
    if structure == "ready":
        return "partial"
    if structure == "failed":
        return "failed"
    return "not_analyzed"


def derive_task_status(task: Mapping[str, Any]) -> str:
    return (
        "running"
        if any(
            str(task.get(field) or "") in ACTIVE_ANALYSIS_STATUSES
            for field in TASK_ANALYSIS_STATUS_FIELDS
        )
        else "draft"
    )


def derive_asset_status(task: Mapping[str, Any]) -> str:
    statuses = [
        str(task.get("dialogue_status") or "not_analyzed"),
        str(task.get("visual_status") or "not_analyzed"),
        str(task.get("composite_status") or "not_analyzed"),
    ]
    if any(status in ACTIVE_ANALYSIS_STATUSES for status in statuses):
        return "running"
    if "blocked" in statuses:
        # A missing-audio Dialogue branch must not hide a ready visual branch.
        # Scheme-level blocked details remain visible independently.
        if any(
            status in {"ready", "partial", "stale"}
            for status in statuses
        ):
            return "partial"
        return "blocked"
    if all(status in {"failed", "not_analyzed"} for status in statuses) and (
        "failed" in statuses
    ):
        return "failed"
    if "stale" in statuses:
        return "stale"
    if all(status == "ready" for status in statuses):
        return "ready"
    if any(status in {"ready", "partial", "failed"} for status in statuses):
        return "partial"
    return "not_analyzed"
