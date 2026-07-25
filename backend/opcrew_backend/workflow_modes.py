from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WORKFLOW_ANALYSIS_V1 = "analysis_v1"
WORKFLOW_SCRIPT = "script"
WORKFLOW_DANCE_MIMIC_V1 = "dance_mimic_v1"
WORKFLOW_PERSON_TALKING_HEAD_V1 = "person_talking_head_v1"

TASK_META_REL = "SessionOutput/task_list/task_meta.json"
SOURCE_SCRIPT_REL = "SessionOutput/subtitle/source_script.txt"

ANALYSIS_V1_COMPATIBLE_WORKFLOWS = {WORKFLOW_ANALYSIS_V1, WORKFLOW_SCRIPT}


def normalize_openclip_workflow_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"dance_mimic", WORKFLOW_DANCE_MIMIC_V1}:
        return WORKFLOW_DANCE_MIMIC_V1
    if mode in {"person_talking_head", "talking_head", WORKFLOW_PERSON_TALKING_HEAD_V1}:
        return WORKFLOW_PERSON_TALKING_HEAD_V1
    if mode in {"script", "script_only", "koubo_script"}:
        return WORKFLOW_SCRIPT
    if mode in {"analysis", WORKFLOW_ANALYSIS_V1, "openclip_analysis", "video", "openclip"}:
        return WORKFLOW_ANALYSIS_V1
    return ""


def read_openclip_task_meta(workspace: Path | str | None) -> dict[str, Any]:
    if not workspace:
        return {}
    try:
        payload = json.loads((Path(workspace) / TASK_META_REL).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def infer_openclip_workflow_mode(
    task: dict[str, Any] | None = None,
    *,
    workspace: Path | str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    task = task or {}
    explicit = normalize_openclip_workflow_mode(task.get("workflow_mode"))
    if explicit:
        return explicit

    task_meta = meta if isinstance(meta, dict) else read_openclip_task_meta(workspace)
    for key in ("workflow_id", "workflow_mode", "create_mode", "input_mode"):
        mode = normalize_openclip_workflow_mode(task_meta.get(key))
        if mode:
            return mode

    if workspace:
        try:
            if (Path(workspace) / SOURCE_SCRIPT_REL).exists() and not str(task.get("reference_video_path") or "").strip():
                return WORKFLOW_SCRIPT
        except Exception:
            pass
    return WORKFLOW_ANALYSIS_V1


def is_analysis_v1_compatible_workflow(mode: Any) -> bool:
    return normalize_openclip_workflow_mode(mode) in ANALYSIS_V1_COMPATIBLE_WORKFLOWS


def storyboard_meta_for_workflow(mode: Any) -> dict[str, str]:
    normalized = normalize_openclip_workflow_mode(mode) or WORKFLOW_ANALYSIS_V1
    if normalized == WORKFLOW_DANCE_MIMIC_V1:
        return {
            "workflow_mode": WORKFLOW_DANCE_MIMIC_V1,
            "title": "故事板（舞蹈复刻）",
            "source_type": "dance_mimic_v1_storyboard",
        }
    if normalized == WORKFLOW_PERSON_TALKING_HEAD_V1:
        return {
            "workflow_mode": WORKFLOW_PERSON_TALKING_HEAD_V1,
            "title": "故事版（人物口播）",
            "source_type": "person_talking_head_storyboard",
        }
    return {
        "workflow_mode": normalized,
        "title": "故事版（口播）",
        "source_type": "analysis_v1_storyboard",
    }
