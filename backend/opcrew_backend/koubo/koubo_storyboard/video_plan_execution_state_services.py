from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from opcrew_backend.adapters.opencode import OpenCodeSessionClient
from opcrew_backend.context import now_ms
from opcrew_backend.routes.media_model_config import CONFIG_TABLE, ensure_table

from .constants import *
from .io_utils import read_json, safe_workspace_rel, write_json
from .runtime import analysis_tool_env
from .text_utils import redact_payload, redact_secret_text

VIDEO_PLAN_EXECUTION_ORPHAN_GRACE_SECONDS = 30


SERVICE_EXPORTS = (
    "video_plan_execution_payload",
    "video_plan_execution_is_running",
    "write_video_plan_execution_state",
)


def _state_epoch_seconds(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return float(parsed.timestamp())
    except Exception:
        return 0.0


def _state_age_seconds(state: dict[str, Any]) -> float:
    updated_at = _state_epoch_seconds(state.get("updated_at") or state.get("started_at"))
    if updated_at <= 0:
        return float("inf")
    return max(0.0, time.time() - updated_at)


def _job_is_active(job: Any) -> bool:
    if job is None:
        return False
    done = getattr(job, "done", None)
    if callable(done):
        try:
            if bool(done()):
                return False
        except Exception:
            pass
    cancelled = getattr(job, "cancelled", None)
    if callable(cancelled):
        try:
            if bool(cancelled()):
                return False
        except Exception:
            pass
    return True


def _video_plan_execution_job_is_active(state: dict[str, Any], *, sc: Any) -> bool:
    job_id = sc.text(state.get("job_id"))
    if not job_id:
        return False
    jobs = getattr(sc, "video_plan_execution_jobs", {}) or {}
    return _job_is_active(jobs.get(job_id))


def _video_plan_execution_is_orphaned(workspace: Path, state: dict[str, Any], *, sc: Any) -> bool:
    if sc.text(state.get("status")) not in {"queued", "running"}:
        return False
    if _video_plan_execution_job_is_active(state, sc=sc):
        return False
    age_seconds = _state_age_seconds(state)
    if age_seconds == float("inf"):
        # Legacy states may not contain timestamps. Their durable file mtime
        # still distinguishes a freshly queued job from a restart orphan.
        try:
            age_seconds = max(
                0.0,
                time.time() - (workspace / VIDEO_PLAN_EXECUTION_STATE_REL).stat().st_mtime,
            )
        except OSError:
            pass
    return age_seconds > VIDEO_PLAN_EXECUTION_ORPHAN_GRACE_SECONDS


def _mark_video_plan_execution_orphaned(state: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    message = (
        "VideoPlan execution was interrupted because the backend worker job is no longer active. "
        "Run the plan again to continue."
    )
    now_value = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    next_state = json.loads(json.dumps(state))
    current_segment_id = sc.text(next_state.get("current_segment_id"))
    current_step = sc.text(next_state.get("current_step"))
    next_state.update({
        "status": "failed",
        "updated_at": now_value,
        "interrupted_at": now_value,
        "orphaned": True,
        "error": message,
    })
    if current_step:
        next_state["current_step_status"] = "failed"
    segments = next_state.get("segments")
    if isinstance(segments, dict) and current_segment_id and isinstance(segments.get(current_segment_id), dict):
        segment = segments[current_segment_id]
        segment["status"] = "failed"
        segment["error"] = message
        steps = segment.get("steps")
        if isinstance(steps, dict) and current_step and isinstance(steps.get(current_step), dict):
            steps[current_step].update({"status": "failed", "updated_at": now_value, "error": message})
    return next_state


def normalize_video_plan_execution_state(workspace: Path, state: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    if not _video_plan_execution_is_orphaned(workspace, state, sc=sc):
        return state
    next_state = _mark_video_plan_execution_orphaned(state, sc=sc)
    sc.write_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL, next_state)
    return next_state


def video_plan_execution_payload(workspace: Path, plan_payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    plan = sc.video_plan_with_hash(plan_payload, sc=sc)
    current_hash = sc.text(plan.get("plan_hash"))
    execution_state = normalize_video_plan_execution_state(workspace, sc.read_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL), sc=sc)
    execution_result = sc.read_json(workspace / VIDEO_PLAN_EXECUTION_RESULT_REL)
    state_hash = sc.text(execution_state.get("source_plan_hash"))
    result_hash = sc.text(execution_result.get("source_plan_hash"))
    return {
        "ok": True,
        "plan_hash": current_hash,
        "artifact_status": sc.video_plan_artifact_status(workspace, plan, sc=sc),
        "execution_state": sc.redact_payload(execution_state),
        "execution_result": sc.redact_payload(execution_result),
        "binding_status": {
            "state_matches_current_plan": bool(current_hash and state_hash and current_hash == state_hash),
            "result_matches_current_plan": bool(current_hash and result_hash and current_hash == result_hash),
            "current_plan_hash": current_hash,
            "state_plan_hash": state_hash,
            "result_plan_hash": result_hash,
        },
    }


def video_plan_execution_is_running(state: dict[str, Any], *, sc: Any) -> bool:
    if sc.text(state.get("status")) not in {"queued", "running"}:
        return False
    if _video_plan_execution_job_is_active(state, sc=sc):
        return True
    return _state_age_seconds(state) <= VIDEO_PLAN_EXECUTION_ORPHAN_GRACE_SECONDS


def write_video_plan_execution_state(workspace: Path, payload: dict[str, Any], *, sc: Any) -> None:
    sc.write_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL, payload)


def register_video_plan_execution_state_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
