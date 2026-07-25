from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from opcrew_backend.workflow_modes import (
    WORKFLOW_DANCE_MIMIC_V1,
    infer_openclip_workflow_mode,
    normalize_openclip_workflow_mode,
)

from .constants import DANCE_MIMIC_STALE_MANIFEST_REL
from .io_utils import read_json, write_json


def _text(value: Any, fallback: str = "") -> str:
    return str(value if value is not None else fallback).strip()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def is_dance_mimic_storyboard(
    task: dict[str, Any],
    workspace: Path,
    *,
    meta: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
) -> bool:
    for payload in (meta, plan, task):
        if not isinstance(payload, dict):
            continue
        for key in ("workflow_mode", "workflow_id"):
            if normalize_openclip_workflow_mode(payload.get(key)) == WORKFLOW_DANCE_MIMIC_V1:
                return True
    return infer_openclip_workflow_mode(task, workspace=workspace) == WORKFLOW_DANCE_MIMIC_V1


def dance_mimic_stale_summary(workspace: Path) -> dict[str, Any]:
    manifest_path = workspace / DANCE_MIMIC_STALE_MANIFEST_REL
    manifest = read_json(manifest_path)
    items = _dict_value(manifest.get("items"))
    events = _list_value(manifest.get("events"))
    return {
        "path": DANCE_MIMIC_STALE_MANIFEST_REL,
        "exists": bool(manifest_path.is_file()),
        "schema_version": _text(manifest.get("schema_version") or "dance_mimic_v1_stale_manifest_0.1"),
        "workflow_id": _text(manifest.get("workflow_id") or WORKFLOW_DANCE_MIMIC_V1),
        "items": items,
        "events": events[-20:],
        "active_count": len(items),
        "updated_at": manifest.get("updated_at"),
    }


def dance_mimic_stale_warnings(stale: dict[str, Any]) -> list[dict[str, Any]]:
    items = _dict_value(stale.get("items"))
    if not items:
        return []
    return [
        {
            "code": "dance_mimic_storyboard_stale",
            "message": "DanceMimic reference outputs changed. Regenerate stale downstream outputs before execution.",
            "items": sorted(items.keys()),
            "stale_manifest_path": DANCE_MIMIC_STALE_MANIFEST_REL,
        }
    ]


def dance_mimic_stale_item_is_active(stale: dict[str, Any], item_id: str) -> bool:
    item = _dict_value(_dict_value(stale.get("items")).get(item_id))
    return _text(item.get("status")) == "stale"


def clear_dance_mimic_stale_items(workspace: Path, item_ids: list[str], *, source_step: str) -> list[str]:
    manifest_path = workspace / DANCE_MIMIC_STALE_MANIFEST_REL
    if not manifest_path.exists():
        return []
    manifest = read_json(manifest_path)
    items = dict(_dict_value(manifest.get("items")))
    cleared = [item_id for item_id in item_ids if item_id in items]
    if not cleared:
        return []
    for item_id in cleared:
        items.pop(item_id, None)
    timestamp = _now_iso()
    events = _list_value(manifest.get("events"))
    events.append({
        "event": "cleared_stale",
        "source_step": source_step,
        "items": cleared,
        "created_at": timestamp,
    })
    manifest["schema_version"] = _text(manifest.get("schema_version") or "dance_mimic_v1_stale_manifest_0.1")
    manifest["workflow_id"] = _text(manifest.get("workflow_id") or WORKFLOW_DANCE_MIMIC_V1)
    manifest["items"] = items
    manifest["events"] = events
    manifest["updated_at"] = timestamp
    write_json(manifest_path, manifest)
    return cleared


def mark_dance_mimic_stale_items(
    workspace: Path,
    items_to_mark: dict[str, list[str]],
    *,
    source_step: str,
    reason: str,
) -> list[str]:
    if not items_to_mark:
        return []
    manifest_path = workspace / DANCE_MIMIC_STALE_MANIFEST_REL
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    items = dict(_dict_value(manifest.get("items")))
    timestamp = _now_iso()
    for item_id, paths in items_to_mark.items():
        items[item_id] = {
            "status": "stale",
            "source_step": source_step,
            "reason": reason,
            "paths": paths,
            "updated_at": timestamp,
        }
    events = _list_value(manifest.get("events"))
    marked = sorted(items_to_mark.keys())
    events.append({
        "event": "marked_stale",
        "source_step": source_step,
        "reason": reason,
        "items": marked,
        "created_at": timestamp,
    })
    manifest.update({
        "schema_version": _text(manifest.get("schema_version") or "dance_mimic_v1_stale_manifest_0.1"),
        "workflow_id": _text(manifest.get("workflow_id") or WORKFLOW_DANCE_MIMIC_V1),
        "items": items,
        "events": events,
        "updated_at": timestamp,
    })
    write_json(manifest_path, manifest)
    return marked
