from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
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


def _text_value(value: Any) -> str:
    return str(value or "").strip()


def active_media_provider_config(app_ctx: Any, kind: str, *, ensure: bool = True) -> dict[str, str]:
    if app_ctx is None or not getattr(app_ctx, "engine", None):
        return {}
    if ensure:
        ensure_table(app_ctx)
    with app_ctx.engine.begin() as conn:
        row = conn.execute(
            sql_text(
                f"""
SELECT provider, model
FROM {CONFIG_TABLE}
WHERE kind = :kind AND enabled = TRUE AND active = TRUE
ORDER BY id ASC
LIMIT 1
"""
            ),
            {"kind": kind},
        ).first()
    if not row:
        return {}
    mapping = row._mapping if hasattr(row, "_mapping") else row
    provider = _text_value(mapping.get("provider") if hasattr(mapping, "get") else mapping[0])
    model = _text_value(mapping.get("model") if hasattr(mapping, "get") else mapping[1])
    return {"provider": provider, "model": model} if provider and model else {}


def active_media_provider_cli_args(app_ctx: Any, kinds: tuple[str, ...] = ("image", "video", "lipsync", "tts")) -> list[str]:
    if app_ctx is None or not getattr(app_ctx, "engine", None):
        return []
    ensure_table(app_ctx)
    cli_args: list[str] = []
    for kind in kinds:
        selection = active_media_provider_config(app_ctx, kind, ensure=False)
        provider = selection.get("provider", "")
        model = selection.get("model", "")
        if provider and model:
            cli_args.extend([f"--{kind}-provider", provider, f"--{kind}-model", model])
    return cli_args


def media_provider_selection_cli_args(selection: dict[str, Any] | None, kinds: tuple[str, ...]) -> list[str]:
    if not isinstance(selection, dict):
        return []
    cli_args: list[str] = []
    for kind in kinds:
        nested = selection.get(kind) if isinstance(selection.get(kind), dict) else {}
        provider = _text_value(selection.get(f"{kind}_provider") or nested.get("provider"))
        model = _text_value(selection.get(f"{kind}_model") or nested.get("model"))
        if provider and model:
            cli_args.extend([f"--{kind}-provider", provider, f"--{kind}-model", model])
    return cli_args


def public_media_alias_selection(selection: dict[str, Any] | None, kinds: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(selection, dict):
        return {}
    aliases: dict[str, str] = {}
    for kind in kinds:
        alias = _text_value(selection.get(f"agent{kind.title()}Alias"))
        if alias:
            aliases[f"agent{kind.title()}Alias"] = alias
    return aliases


def preserve_video_plan_public_aliases(workspace: Path, model_selection: dict[str, Any] | None, *, sc: Any) -> None:
    aliases = public_media_alias_selection(model_selection, ("image", "video"))
    if not aliases:
        return
    state = read_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL)
    if isinstance(state, dict) and any(_text_value(state.get(key)) != value for key, value in aliases.items()):
        sc.write_video_plan_execution_state(workspace, {**state, **aliases}, sc=sc)
    result = read_json(workspace / VIDEO_PLAN_EXECUTION_RESULT_REL)
    if isinstance(result, dict) and any(_text_value(result.get(key)) != value for key, value in aliases.items()):
        write_json(workspace / VIDEO_PLAN_EXECUTION_RESULT_REL, {**result, **aliases})


SERVICE_EXPORTS = (
    "load_analysis_tool_module",
    "tool_returncode",
    "run_composer_tool",
    "run_composer_background",
    "run_video_plan_execution_tool",
    "run_video_plan_execution_background",
    "ensure_talking_head_session_variables",
    "reset_video_plan_outputs",
    "run_video_plan_tool",
    "reset_image_plan_outputs",
    "run_image_plan_tool",
    "run_image_plan_execution_tool",
    "run_image_plan_execution_background",
    "reset_video_only_plan_outputs",
    "run_video_only_plan_tool",
    "run_video_only_plan_execution_tool",
    "reload_video_only_plan_prompt",
    "run_video_only_plan_execution_background",
)


def _error_message(value: Any) -> str:
    safe_value = redact_payload(value)
    if isinstance(safe_value, list):
        return "；".join(filter(None, (_error_message(item) for item in safe_value)))
    if isinstance(safe_value, dict):
        for key in ("message", "detail", "reason", "error", "code"):
            message = _error_message(safe_value.get(key))
            if message:
                return message
        return ""
    return _text_value(safe_value)


def _talking_head_default_video_is_current(variables: Any) -> bool:
    if not isinstance(variables, dict):
        return False
    default_video = variables.get("default_video_config") if isinstance(variables.get("default_video_config"), dict) else {}
    talking_head = variables.get("talking_head") if isinstance(variables.get("talking_head"), dict) else {}
    selected_video = talking_head.get("video_model") if isinstance(talking_head.get("video_model"), dict) else {}
    default_provider = _text_value(default_video.get("provider"))
    default_model = _text_value(default_video.get("model"))
    selected_provider = _text_value(selected_video.get("provider"))
    selected_model = _text_value(selected_video.get("model"))
    model_matches = (
        not selected_model
        or selected_model == default_model
        or {selected_model, default_model}.issubset({"wan2.7-r2v", "wan2.7-r2v-2026-06-12"})
    )
    return bool(
        default_provider
        and default_model
        and (not selected_provider or selected_provider == default_provider)
        and model_matches
    )


def ensure_talking_head_session_variables(task: dict[str, Any], workspace: Path, *, sc: Any) -> dict[str, Any]:
    variables_path = workspace / "SessionContext" / "Variables.json"
    variables = read_json(variables_path)
    workflow_id = _text_value(
        task.get("workflow_mode")
        or (variables.get("workflow_id") if isinstance(variables, dict) else "")
        or (variables.get("profile_id") if isinstance(variables, dict) else "")
    )
    if workflow_id != "person_talking_head_v1":
        return {"status": "not_applicable", "refreshed": False}
    if _talking_head_default_video_is_current(variables):
        return {"status": "ready", "refreshed": False}
    script = TALKING_HEAD_SESSION_VARIABLES_PREPARE_SCRIPT_PATH
    if not script.is_file():
        raise RuntimeError(f"TalkingHead session variables tool not found: {script}")
    task_id = int(task.get("id") or 0)
    session_id = int(task.get("session_id") or 0)
    sc.add_event(session_id, "talking_head_v1.session_variables.auto_refresh_started", {
        "task_id": task_id,
        "reason": "default_video_config_missing_or_stale",
    })
    completed = subprocess.run(
        [sys.executable, str(script), "--workspace", str(workspace), "--force", "--print-json"],
        cwd=str(OPENCREW_ROOT),
        env=analysis_tool_env(),
        check=False,
        capture_output=True,
        text=True,
        timeout=900,
    )
    refreshed = read_json(variables_path)
    if completed.returncode != 0 or not _talking_head_default_video_is_current(refreshed):
        try:
            result = json.loads((completed.stdout or "")[(completed.stdout or "").find("{"):]) if "{" in (completed.stdout or "") else {}
        except Exception:
            result = {}
        message = _error_message(result.get("blocked_reasons") or result.get("error") or completed.stderr or completed.stdout)
        raise RuntimeError(message or "人物口播运行配置自动恢复失败。")
    sc.add_event(session_id, "talking_head_v1.session_variables.auto_refresh_completed", {
        "task_id": task_id,
        "variables_path": "SessionContext/Variables.json",
    })
    return {"status": "refreshed", "refreshed": True}


def load_analysis_tool_module(script_path: Path, module_hint: str) -> Any:
    if not script_path.exists():
        raise HTTPException(status_code=500, detail=f"Analysis tool not found: {script_path}")
    module_name = f"opencrew_analysis_v1_{module_hint}_{int(script_path.stat().st_mtime_ns)}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if not spec or not spec.loader:
        raise HTTPException(status_code=500, detail=f"Could not load Analysis tool: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def tool_returncode(result: dict[str, Any]) -> int:
    return 0 if _text_value(result.get("status")) not in {"failed", "blocked"} else 1


def run_composer_tool(workspace: Path, target: dict[str, str], settings: dict[str, Any]) -> tuple[dict[str, Any], str, str, int]:
    if not COMPOSER_SCRIPT_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Composer tool not found: {COMPOSER_SCRIPT_PATH}")
    cmd = [
        sys.executable or "python3",
        str(COMPOSER_SCRIPT_PATH),
        "--workspace",
        str(workspace),
        "--target-type",
        target["target_type"],
        "--subtitle-mode",
        settings["subtitle_mode"],
        "--watermark-mode",
        settings["watermark_mode"],
        "--print-json",
    ]
    if settings.get("force", True):
        cmd.append("--force")
    if target["shot_id"]:
        cmd.extend(["--shot-id", target["shot_id"]])
    if target["scene_id"]:
        cmd.extend(["--scene-id", target["scene_id"]])
    completed = subprocess.run(cmd, cwd=str(COMPOSER_SCRIPT_PATH.parent), env=analysis_tool_env(), check=False, capture_output=True, text=True, timeout=7200)
    stdout = completed.stdout or ""
    try:
        result = json.loads(stdout[stdout.find("{"):]) if "{" in stdout else {}
    except Exception:
        result = {}
    return result if isinstance(result, dict) else {}, stdout, completed.stderr or "", completed.returncode


async def run_composer_background(task_id: int, session_id: int, workspace: Path, job_id: str, target: dict[str, str], settings: dict[str, Any], *, sc: Any) -> None:
    started = time.time()
    try:
        state = read_json(workspace / COMPOSER_EXECUTION_STATE_REL)
        state = {
            **(state if isinstance(state, dict) else {}),
            "status": "running",
            "updated_at": sc.composer_now_iso(),
        }
        sc.write_composer_execution_state(workspace, state, sc=sc)
        result, stdout, stderr, returncode = await asyncio.to_thread(run_composer_tool, workspace, target, settings)
        status = sc.text(result.get("status"), "failed")
        terminal_status = "completed" if returncode == 0 and status not in {"failed", "blocked"} else "failed"
        error = ""
        if terminal_status == "failed":
            error = result.get("blocked_reasons") or stderr or stdout or "Composer failed."
        state = {
            **state,
            "status": terminal_status,
            "returncode": returncode,
            "tool_status": status,
            "summary": result.get("summary") if isinstance(result.get("summary"), dict) else {},
            "error": redact_payload(error),
            "elapsed_seconds": round(time.time() - started, 3),
            "updated_at": sc.composer_now_iso(),
        }
        sc.write_composer_execution_state(workspace, state, sc=sc)
        event_name = "koubo_storyboard.composer.completed" if terminal_status == "completed" else "koubo_storyboard.composer.failed"
        sc.add_event(session_id, event_name, {
            "task_id": task_id,
            "workspace_dir": str(workspace),
            "job_id": job_id,
            "target": target,
            "settings": settings,
            "returncode": returncode,
            "status": status,
            "elapsed_seconds": state["elapsed_seconds"],
            "summary": state["summary"],
            "detail": redact_payload(error) if error else "",
        })
    except Exception as exc:
        state = read_json(workspace / COMPOSER_EXECUTION_STATE_REL)
        if sc.composer_execution_is_running(state, sc=sc):
            state = {
                **state,
                "status": "failed",
                "error": str(exc),
                "updated_at": sc.composer_now_iso(),
            }
            sc.write_composer_execution_state(workspace, state, sc=sc)
        sc.add_event(session_id, "koubo_storyboard.composer.failed", {
            "task_id": task_id,
            "workspace_dir": str(workspace),
            "job_id": job_id,
            "target": target,
            "settings": settings,
            "error": str(exc),
        })
    finally:
        sc.composer_execution_jobs.pop(job_id, None)


def video_plan_execution_script_path(workspace: Path) -> Path:
    # StoryBoard's Video Plan is an Analysis_V1-owned surface.  A workflow
    # profile may change Session Variables refresh and prompt reload, but it
    # must never replace the executor that consumes the Analysis_V1 05_01
    # plan.  TalkingHead_V1 05_02 is reserved for the talking-head one-click
    # movie pipeline.
    _ = workspace
    return VIDEO_PLAN_EXECUTION_SCRIPT_PATH


def storyboard_workflow_id(workspace: Path) -> str:
    variables = read_json(workspace / "SessionContext" / "Variables.json")
    if isinstance(variables, dict):
        workflow_id = _text_value(variables.get("workflow_id") or variables.get("profile_id"))
        if workflow_id:
            return workflow_id
    storyboard = read_json(workspace / STORYBOARD_REL)
    if isinstance(storyboard, dict):
        return _text_value(storyboard.get("workflow_id") or storyboard.get("workflow_mode") or storyboard.get("profile_id"))
    return ""


def run_video_plan_execution_tool(
    workspace: Path,
    job_id: str,
    source_plan_hash: str,
    model_selection: dict[str, Any] | None = None,
    *,
    sc: Any,
) -> tuple[dict[str, Any], str, str, int]:
    execution_script_path = video_plan_execution_script_path(workspace)
    if not execution_script_path.exists():
        raise HTTPException(status_code=500, detail=f"VideoPlan executor not found: {execution_script_path}")
    cmd = [
        sys.executable,
        str(execution_script_path),
        "--workspace",
        str(workspace),
        "--force",
        "--execution-job-id",
        job_id,
        "--source-plan-hash",
        source_plan_hash,
        "--no-execute-audio",
        "--print-json",
    ]
    # Analysis_V1 executors own a session snapshot in
    # SessionContext/Variables.json.  Do not replace that snapshot with the
    # database-wide active model (or UI settings) at execution time.  The
    # database is still available to the executor for resolving the selected
    # config's secret into process memory.
    completed = subprocess.run(cmd, cwd=str(execution_script_path.parent), env=analysis_tool_env(), check=False, capture_output=True, text=True, timeout=7200)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    try:
        result = json.loads(stdout[stdout.find("{"):]) if "{" in stdout else {}
    except Exception:
        result = {}
    return result if isinstance(result, dict) else {}, stdout, stderr, completed.returncode


def mark_current_video_plan_step_failed(state: dict[str, Any], error: Any) -> dict[str, Any]:
    segment_id = str(state.get("current_segment_id") or "").strip()
    step = str(state.get("current_step") or "").strip()
    message = _error_message(error)
    if not segment_id or not step:
        return state
    segment_state = state.setdefault("segments", {}).setdefault(segment_id, {
        "segment_id": segment_id,
        "status": "failed",
        "steps": {},
        "outputs": {},
        "error": "",
    })
    segment_state["status"] = "failed"
    segment_state["error"] = message
    segment_state.setdefault("steps", {})[step] = {
        **(segment_state.get("steps", {}).get(step) if isinstance(segment_state.get("steps", {}).get(step), dict) else {}),
        "status": "failed",
        "error": message,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state["current_step_status"] = "failed"
    return state


async def run_video_plan_execution_background(
    task_id: int,
    session_id: int,
    workspace: Path,
    job_id: str,
    source_plan_hash: str,
    model_selection: dict[str, Any] | None = None,
    *,
    sc: Any,
) -> None:
    try:
        task = sc.task_or_404(task_id)
        await asyncio.to_thread(ensure_talking_head_session_variables, task, workspace, sc=sc)
        audio_preparation = await asyncio.to_thread(sc.prepare_video_plan_selected_tts_audio, task, workspace, sc=sc)
        if int(audio_preparation.get("generated_count") or 0) > 0:
            sc.add_event(session_id, "koubo_storyboard.video_plan.tts_audio_prepared", {
                "task_id": task_id,
                "workspace_dir": str(workspace),
                "job_id": job_id,
                "source_plan_hash": source_plan_hash,
                "generated_count": int(audio_preparation.get("generated_count") or 0),
                "dialogue_asset_keys": [
                    _text_value(item.get("dialogue_asset_key"))
                    for item in audio_preparation.get("items") or []
                    if isinstance(item, dict) and _text_value(item.get("dialogue_asset_key"))
                ],
            })
        result, stdout, stderr, returncode = await asyncio.to_thread(run_video_plan_execution_tool, workspace, job_id, source_plan_hash, model_selection, sc=sc)
        state = read_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL)
        if returncode != 0 and sc.video_plan_execution_is_running(state, sc=sc):
            error = _error_message(result.get("blocked_reasons") or stderr or stdout or "05_02 execution failed.")
            state = {
                **state,
                "status": "failed",
                "error": error,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            state = mark_current_video_plan_step_failed(state, error)
            sc.write_video_plan_execution_state(workspace, state, sc=sc)
        preserve_video_plan_public_aliases(workspace, model_selection, sc=sc)
        sc.add_event(session_id, "koubo_storyboard.video_plan.execution_finished", {
            "task_id": task_id,
            "workspace_dir": str(workspace),
            "job_id": job_id,
            "source_plan_hash": source_plan_hash,
            "returncode": returncode,
            "status": result.get("status") or read_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL).get("status"),
        })
    except Exception as exc:
        state = read_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL)
        if sc.video_plan_execution_is_running(state, sc=sc):
            state = {**state, "status": "failed", "error": str(exc), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            state = mark_current_video_plan_step_failed(state, str(exc))
            sc.write_video_plan_execution_state(workspace, state, sc=sc)
        sc.add_event(session_id, "koubo_storyboard.video_plan.execution_failed", {
            "task_id": task_id,
            "workspace_dir": str(workspace),
            "job_id": job_id,
            "source_plan_hash": source_plan_hash,
            "error": str(exc),
        })
    finally:
        sc.video_plan_execution_jobs.pop(job_id, None)


def reset_video_plan_outputs(workspace: Path) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for rel in (
        VIDEO_PLAN_TOOL_DIR_REL,
        VIDEO_PLAN_REL,
        VIDEO_PLAN_UI_CACHE_REL,
        VIDEO_PLAN_EXECUTION_TOOL_DIR_REL,
        VIDEO_PLAN_EXECUTION_STATE_REL,
        VIDEO_PLAN_EXECUTION_RESULT_REL,
    ):
        path = workspace / rel
        if not path.exists():
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        actions.append({"path": rel, "action": "removed_for_video_plan_rerun"})
    return actions


def run_video_plan_tool(workspace: Path, target: dict[str, str], settings: dict[str, float]) -> tuple[dict[str, Any], str, str, int]:
    if not VIDEO_PLAN_SCRIPT_PATH.exists():
        raise HTTPException(status_code=500, detail=f"VideoPlan tool not found: {VIDEO_PLAN_SCRIPT_PATH}")
    try:
        module = load_analysis_tool_module(VIDEO_PLAN_SCRIPT_PATH, "video_plan_generator")
        args = module.Args(
            workspace=str(workspace),
            target_type=target["target_type"],
            shot_id=target["shot_id"],
            scene_id=target["scene_id"],
            max_video_seconds=float(settings["max_video_seconds"]),
            min_video_seconds=float(settings["min_video_seconds"]),
            split_tolerance_seconds=float(settings["split_tolerance_seconds"]),
            force=True,
            resume=False,
            print_json=False,
        )
        result = module.run(args)
    except Exception as exc:
        result = {"status": "failed", "blocked_reasons": [{"code": "in_process_tool_error", "message": str(exc)}]}
    result = result if isinstance(result, dict) else {}
    return result, "", "", tool_returncode(result)


def reset_image_plan_outputs(workspace: Path) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for rel in (
        IMAGE_PLAN_TOOL_DIR_REL,
        IMAGE_PLAN_REL,
        IMAGE_PLAN_EXECUTION_TOOL_DIR_REL,
        IMAGE_PLAN_EXECUTION_STATE_REL,
        IMAGE_PLAN_EXECUTION_RESULT_REL,
    ):
        path = workspace / rel
        if not path.exists():
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        actions.append({"path": rel, "action": "removed_for_image_plan_rerun"})
    return actions


def run_image_plan_tool(workspace: Path, target: dict[str, str], settings: dict[str, float]) -> tuple[dict[str, Any], str, str, int]:
    if not IMAGE_PLAN_SCRIPT_PATH.exists():
        raise HTTPException(status_code=500, detail=f"ImagePlan tool not found: {IMAGE_PLAN_SCRIPT_PATH}")
    try:
        module = load_analysis_tool_module(IMAGE_PLAN_SCRIPT_PATH, "image_plan_generator")
        args = module.Args(
            workspace=str(workspace),
            target_type=target["target_type"],
            shot_id=target["shot_id"],
            scene_id=target["scene_id"],
            max_video_seconds=float(settings["max_video_seconds"]),
            min_video_seconds=float(settings["min_video_seconds"]),
            split_tolerance_seconds=float(settings["split_tolerance_seconds"]),
            include_existing_prompts=True,
            include_ready_images=True,
            force=True,
            resume=False,
            print_json=False,
        )
        result = module.run(args)
    except Exception as exc:
        result = {"status": "failed", "blocked_reasons": [{"code": "in_process_tool_error", "message": str(exc)}]}
    result = result if isinstance(result, dict) else {}
    return result, "", "", tool_returncode(result)


def run_image_plan_execution_tool(
    workspace: Path,
    mode: str = "prompt-only",
    source_plan_hash: str = "",
    target_task_id: str = "",
    target_asset_key: str = "",
    model_selection: dict[str, Any] | None = None,
    *,
    sc: Any = None,
) -> tuple[dict[str, Any], str, str, int]:
    if not IMAGE_PLAN_EXECUTION_SCRIPT_PATH.exists():
        raise HTTPException(status_code=500, detail=f"ImagePlan executor not found: {IMAGE_PLAN_EXECUTION_SCRIPT_PATH}")
    normalized_mode = mode if mode in {"prompt-only", "image-only", "prompt-and-image"} else "prompt-only"
    cmd = [
        sys.executable or "python3",
        str(IMAGE_PLAN_EXECUTION_SCRIPT_PATH),
        "--workspace",
        str(workspace),
        "--mode",
        normalized_mode,
        "--source-plan-hash",
        source_plan_hash,
        "--force",
        "--print-json",
    ]
    if normalized_mode in {"prompt-only", "prompt-and-image"}:
        cmd.append("--overwrite-prompt")
    if normalized_mode in {"image-only", "prompt-and-image"}:
        cmd.append("--overwrite-image")
    if target_task_id:
        cmd.extend(["--target-task-id", target_task_id])
    if target_asset_key:
        cmd.extend(["--target-asset-key", target_asset_key])
    supported_kinds = ("image",)
    cmd.extend(active_media_provider_cli_args(getattr(sc, "ctx", None), supported_kinds))
    cmd.extend(media_provider_selection_cli_args(model_selection, supported_kinds))
    completed = subprocess.run(cmd, cwd=str(IMAGE_PLAN_EXECUTION_SCRIPT_PATH.parent), env=analysis_tool_env(), check=False, capture_output=True, text=True, timeout=7200)
    stdout = completed.stdout or ""
    try:
        result = json.loads(stdout[stdout.find("{"):]) if "{" in stdout else {}
    except Exception:
        result = {}
    return result if isinstance(result, dict) else {}, stdout, completed.stderr or "", completed.returncode


async def run_image_plan_execution_background(
    task_id: int,
    session_id: int,
    workspace: Path,
    job_id: str,
    source_plan_hash: str,
    mode: str = "prompt-only",
    target_task_id: str = "",
    target_asset_key: str = "",
    model_selection: dict[str, Any] | None = None,
    *,
    sc: Any,
) -> None:
    started = time.time()
    try:
        result, stdout, stderr, returncode = await asyncio.to_thread(
            run_image_plan_execution_tool,
            workspace,
            mode,
            source_plan_hash,
            target_task_id,
            target_asset_key,
            model_selection,
            sc=sc,
        )
        result_status = sc.text(result.get("status"), "failed")
        execution_result = read_json(workspace / IMAGE_PLAN_EXECUTION_RESULT_REL)
        if isinstance(execution_result, dict) and execution_result:
            execution_result.setdefault("source_plan_hash", source_plan_hash)
            execution_result.setdefault("image_plan_hash", source_plan_hash)
            write_json(workspace / IMAGE_PLAN_EXECUTION_RESULT_REL, execution_result)
        state = read_json(workspace / IMAGE_PLAN_EXECUTION_STATE_REL)
        if isinstance(state, dict):
            state.setdefault("source_plan_hash", source_plan_hash)
            if returncode != 0 or result_status in {"failed", "blocked"}:
                state["status"] = "failed"
                state["error"] = redact_payload(result.get("blocked_reasons") or stderr or stdout or "ImagePlan execution failed.")
            else:
                state["status"] = result_status
            state["returncode"] = returncode
            state["tool_status"] = result_status
            state["elapsed_seconds"] = round(time.time() - started, 3)
            state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            write_json(workspace / IMAGE_PLAN_EXECUTION_STATE_REL, state)
        event_name = "koubo_storyboard.image_plan.execution_finished" if returncode == 0 and result_status not in {"failed", "blocked"} else "koubo_storyboard.image_plan.execution_failed"
        sc.add_event(session_id, event_name, {
            "task_id": task_id,
            "workspace_dir": str(workspace),
            "job_id": job_id,
            "mode": mode,
            "target_task_id": target_task_id,
            "target_asset_key": target_asset_key,
            "source_plan_hash": source_plan_hash,
            "returncode": returncode,
            "status": result_status,
            "elapsed_seconds": round(time.time() - started, 3),
            "summary": result.get("summary") or {},
            "detail": redact_payload(result.get("blocked_reasons") or stderr or stdout) if returncode != 0 or result_status in {"failed", "blocked"} else "",
        })
    except Exception as exc:
        state = read_json(workspace / IMAGE_PLAN_EXECUTION_STATE_REL)
        if isinstance(state, dict) and sc.text(state.get("status")) in {"queued", "running"}:
            state = {**state, "status": "failed", "error": str(exc), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            write_json(workspace / IMAGE_PLAN_EXECUTION_STATE_REL, state)
        sc.add_event(session_id, "koubo_storyboard.image_plan.execution_failed", {
            "task_id": task_id,
            "workspace_dir": str(workspace),
            "job_id": job_id,
            "mode": mode,
            "target_task_id": target_task_id,
            "target_asset_key": target_asset_key,
            "source_plan_hash": source_plan_hash,
            "error": str(exc),
        })
    finally:
        finish_execution_public_alias_job(sc.image_plan_execution_jobs, job_id, workspace, model_selection, ("image",), IMAGE_PLAN_EXECUTION_STATE_REL, IMAGE_PLAN_EXECUTION_RESULT_REL)


def reset_video_only_plan_outputs(workspace: Path) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for rel in (
        VIDEO_ONLY_PLAN_TOOL_DIR_REL,
        VIDEO_ONLY_PLAN_REL,
        VIDEO_ONLY_PLAN_EXECUTION_TOOL_DIR_REL,
        VIDEO_ONLY_PLAN_EXECUTION_STATE_REL,
        VIDEO_ONLY_PLAN_EXECUTION_RESULT_REL,
    ):
        path = workspace / rel
        if not path.exists():
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        actions.append({"path": rel, "action": "removed_for_video_only_plan_rerun"})
    return actions


def run_video_only_plan_tool(workspace: Path, target: dict[str, str], settings: dict[str, float]) -> tuple[dict[str, Any], str, str, int]:
    if not VIDEO_ONLY_PLAN_SCRIPT_PATH.exists():
        raise HTTPException(status_code=500, detail=f"VideoOnlyPlan tool not found: {VIDEO_ONLY_PLAN_SCRIPT_PATH}")
    try:
        module = load_analysis_tool_module(VIDEO_ONLY_PLAN_SCRIPT_PATH, "video_only_plan_generator")
        args = module.Args(
            workspace=str(workspace),
            target_type=target["target_type"],
            shot_id=target["shot_id"],
            scene_id=target["scene_id"],
            max_video_seconds=float(settings["max_video_seconds"]),
            min_video_seconds=float(settings["min_video_seconds"]),
            split_tolerance_seconds=float(settings["split_tolerance_seconds"]),
            force=True,
            resume=False,
            print_json=False,
        )
        result = module.run(args)
    except Exception as exc:
        result = {"status": "failed", "blocked_reasons": [{"code": "in_process_tool_error", "message": str(exc)}]}
    result = result if isinstance(result, dict) else {}
    return result, "", "", tool_returncode(result)


def run_video_only_plan_execution_tool(
    workspace: Path,
    mode: str = "prompt-only",
    source_plan_hash: str = "",
    target_task_id: str = "",
    target_asset_key: str = "",
    model_selection: dict[str, Any] | None = None,
    *,
    sc: Any = None,
) -> tuple[dict[str, Any], str, str, int]:
    if not VIDEO_ONLY_PLAN_EXECUTION_SCRIPT_PATH.exists():
        raise HTTPException(status_code=500, detail=f"VideoOnlyPlan executor not found: {VIDEO_ONLY_PLAN_EXECUTION_SCRIPT_PATH}")
    normalized_mode = mode if mode in {"prompt-only", "video-only", "prompt-and-video"} else "prompt-only"
    cmd = [
        sys.executable or "python3",
        str(VIDEO_ONLY_PLAN_EXECUTION_SCRIPT_PATH),
        "--workspace",
        str(workspace),
        "--mode",
        normalized_mode,
        "--source-plan-hash",
        source_plan_hash,
        "--print-json",
    ]
    if normalized_mode in {"prompt-only", "prompt-and-video"}:
        cmd.append("--overwrite-prompt")
    if target_task_id:
        cmd.extend(["--target-task-id", target_task_id])
    if target_asset_key:
        cmd.extend(["--target-asset-key", target_asset_key])
    # 05_06 reuses the Analysis_V1 05_02 provider modules and must use the
    # provider/model snapshot in SessionContext/Variables.json.  Passing the
    # database-wide active model here can silently route a Max SD 2 session to
    # Grok and copy the wrong prompt template.
    completed = subprocess.run(cmd, cwd=str(VIDEO_ONLY_PLAN_EXECUTION_SCRIPT_PATH.parent), env=analysis_tool_env(), check=False, capture_output=True, text=True, timeout=7200)
    stdout = completed.stdout or ""
    try:
        result = json.loads(stdout[stdout.find("{"):]) if "{" in stdout else {}
    except Exception:
        result = {}
    return result if isinstance(result, dict) else {}, stdout, completed.stderr or "", completed.returncode


def reload_talking_head_video_only_plan_prompt(workspace: Path, source_plan_hash: str, target_asset_key: str) -> dict[str, Any]:
    module = load_analysis_tool_module(TALKING_HEAD_VIDEO_PLAN_EXECUTION_SCRIPT_PATH, "talking_head_video_prompt_reload")
    variables = module.load_required_json(workspace, module.VARIABLES_REL, "variables_missing")
    storyboard = module.load_required_json(workspace, module.STORYBOARD_REL, "storyboard_missing")
    plan = read_json(workspace / VIDEO_ONLY_PLAN_REL)
    if not isinstance(plan, dict):
        raise HTTPException(status_code=404, detail="VideoOnlyPlan does not exist. Generate the plan first.")
    selected = [
        item for item in (plan.get("video_only_tasks") or [])
        if isinstance(item, dict) and _text_value(item.get("asset_key")) == target_asset_key
    ]
    if len(selected) != 1:
        raise HTTPException(status_code=404, detail="Video-only task not found in current plan.")
    task = selected[0]
    segment = task.get("source_segment") if isinstance(task.get("source_segment"), dict) else {}
    shot = task.get("source_shot") if isinstance(task.get("source_shot"), dict) else {}
    scene = task.get("source_scene") if isinstance(task.get("source_scene"), dict) else {}
    if not segment:
        raise HTTPException(status_code=400, detail="TalkingHead prompt reload requires source_segment in the current plan.")
    default_video = variables.get("default_video_config") if isinstance(variables.get("default_video_config"), dict) else {}
    provider = _text_value(default_video.get("provider"))
    model = _text_value(default_video.get("model"))
    video_module = module.video_module_for(provider, model)
    planned_outputs = task.get("planned_outputs") if isinstance(task.get("planned_outputs"), dict) else {}
    prompt_rel = _text_value(planned_outputs.get("video_prompt_path")) or f"{WORKING_REL}/{target_asset_key}_VideoPrompt.json"
    prompt_path = workspace / prompt_rel
    image_rel = _text_value((task.get("first_frame") or {}).get("planned_image_path")) if isinstance(task.get("first_frame"), dict) else ""
    image_path = workspace / image_rel if image_rel else None
    target_aspect = "9:16"
    if image_path is not None and image_path.exists() and image_path.is_file():
        try:
            _width, _height, target_aspect = module.inspect_video_first_frame(image_path)
        except Exception:
            # A prompt can be reloaded before a legacy placeholder image has
            # been replaced by real media. Keep that workflow available; the
            # execution path validates and normalizes the actual first frame.
            target_aspect = "9:16"
    package = video_module.build_prompt_package({
        "workspace": str(workspace),
        "prompt_dir": str(prompt_path.parent),
        "prompt_template": _text_value(getattr(module, "TALKING_HEAD_REFERENCE_PROMPT_TEMPLATE", "")) or "Video_SDR2V_TalkingHead.md",
        "reference_images": [str(image_path)] if image_path is not None else [],
        "segment": segment,
        "shot": shot,
        "scene": scene,
        "dialogue_index": module.flatten_dialogues(storyboard),
        "aspect": target_aspect,
        "aspect_ratio": target_aspect,
        "requested_aspect": target_aspect,
    })
    package = module.prompt_package_for_video_aspect(package, target_aspect)
    package.update({
        "video_only_task_id": _text_value(task.get("video_only_task_id")),
        "asset_key": target_asset_key,
        "prompt_status": "draft_generated",
        "prompt_origin": "talking_head_workflow_reload",
        "source_plan_hash": _text_value(source_plan_hash or plan.get("plan_hash")),
        "workflow_id": "person_talking_head_v1",
        "prompt_tool": "TalkingHead_V1/05_02_VideoPlanExecutor",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    written_path = video_module.write_prompt_package(prompt_path.parent, target_asset_key, package)
    if written_path != prompt_path:
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(written_path, prompt_path)
    return {
        "status": "completed",
        "operation": "reload_prompt",
        "workflow_id": "person_talking_head_v1",
        "prompt_tool": "TalkingHead_V1/05_02_VideoPlanExecutor",
        "source_plan_hash": package["source_plan_hash"],
        "tasks": [{
            "video_only_task_id": package["video_only_task_id"],
            "asset_key": target_asset_key,
            "status": "completed",
            "steps": {"prompt": {"status": "completed_working", "output_path": module.rel(workspace, prompt_path)}},
        }],
        "summary": {"task_count": 1, "completed_count": 1},
        "updated_at": package["updated_at"],
    }


def reload_video_only_plan_prompt(workspace: Path, source_plan_hash: str = "", target_asset_key: str = "") -> dict[str, Any]:
    if storyboard_workflow_id(workspace) == "person_talking_head_v1":
        return reload_talking_head_video_only_plan_prompt(workspace, source_plan_hash, target_asset_key)
    if not VIDEO_ONLY_PLAN_EXECUTION_SCRIPT_PATH.exists():
        raise HTTPException(status_code=500, detail=f"VideoOnlyPlan executor not found: {VIDEO_ONLY_PLAN_EXECUTION_SCRIPT_PATH}")
    if not target_asset_key:
        raise HTTPException(status_code=400, detail="target_asset_key is required")
    module = load_analysis_tool_module(VIDEO_ONLY_PLAN_EXECUTION_SCRIPT_PATH, "video_only_plan_prompt_reload")
    args = module.Args(
        workspace=str(workspace),
        database_url="",
        mode="prompt-only",
        image_provider="",
        image_model="",
        video_provider="",
        video_model="",
        tts_provider="",
        tts_model="",
        source_plan_hash=source_plan_hash,
        target_task_id="",
        target_asset_key=target_asset_key,
        only_missing=False,
        overwrite_prompt=True,
        overwrite_video=False,
        force=False,
        resume=False,
        provider_timeout_seconds=7200,
        print_json=False,
    )
    module.configure_vpe_hooks()
    module.ensure_tool_dirs(workspace)
    variables = module.load_required_json(workspace, module.VARIABLES_REL, "variables_missing")
    storyboard = module.load_required_json(workspace, module.STORYBOARD_REL, "storyboard_missing")
    plan = module.load_required_json(workspace, module.PLAN_REL, "video_only_plan_missing")
    plan_hash = _text_value(source_plan_hash or plan.get("plan_hash"))
    plan = {**plan, "plan_hash": plan_hash}
    result = module.base_result(workspace, args)
    result["operation"] = "reload_prompt"
    result["source_plan_hash"] = plan_hash
    module.copy_inputs_to_working(workspace, variables, storyboard, plan, result, args)
    selected = module.select_tasks(plan, args)
    if len(selected) != 1:
        raise HTTPException(status_code=404, detail="Video-only task not found in current plan.")
    prompt_path = module.build_video_prompt(workspace, variables, storyboard, plan, selected[0], args, result)
    result["tasks"] = [{
        "video_only_task_id": _text_value(selected[0].get("video_only_task_id")),
        "asset_key": _text_value(selected[0].get("asset_key")),
        "status": "completed",
        "steps": {
            "prompt": {
                "status": "completed_working",
                "output_path": module.rel(workspace, prompt_path),
            }
        },
    }]
    result["summary"] = module.summarize(result["tasks"])
    result["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return result


async def run_video_only_plan_execution_background(
    task_id: int,
    session_id: int,
    workspace: Path,
    job_id: str,
    source_plan_hash: str,
    mode: str = "prompt-only",
    target_task_id: str = "",
    target_asset_key: str = "",
    model_selection: dict[str, Any] | None = None,
    *,
    sc: Any,
) -> None:
    started = time.time()
    try:
        result, stdout, stderr, returncode = await asyncio.to_thread(
            run_video_only_plan_execution_tool,
            workspace,
            mode,
            source_plan_hash,
            target_task_id,
            target_asset_key,
            model_selection,
            sc=sc,
        )
        result_status = sc.text(result.get("status"), "failed")
        execution_result = read_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_RESULT_REL)
        if isinstance(execution_result, dict) and execution_result:
            execution_result.setdefault("source_plan_hash", source_plan_hash)
            execution_result.setdefault("video_only_plan_hash", source_plan_hash)
            write_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_RESULT_REL, execution_result)
        state = read_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_STATE_REL)
        if isinstance(state, dict):
            state.setdefault("source_plan_hash", source_plan_hash)
            if returncode != 0 or result_status in {"failed", "blocked"}:
                state["status"] = "failed"
                state["error"] = redact_payload(result.get("blocked_reasons") or stderr or stdout or "VideoOnlyPlan execution failed.")
            else:
                state["status"] = result_status
            state["returncode"] = returncode
            state["tool_status"] = result_status
            state["elapsed_seconds"] = round(time.time() - started, 3)
            state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            write_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_STATE_REL, state)
        event_name = "koubo_storyboard.video_only_plan.execution_finished" if returncode == 0 and result_status not in {"failed", "blocked"} else "koubo_storyboard.video_only_plan.execution_failed"
        sc.add_event(session_id, event_name, {
            "task_id": task_id,
            "workspace_dir": str(workspace),
            "job_id": job_id,
            "mode": mode,
            "target_task_id": target_task_id,
            "target_asset_key": target_asset_key,
            "source_plan_hash": source_plan_hash,
            "returncode": returncode,
            "status": result_status,
            "elapsed_seconds": round(time.time() - started, 3),
            "summary": result.get("summary") or {},
            "detail": redact_payload(result.get("blocked_reasons") or stderr or stdout) if returncode != 0 or result_status in {"failed", "blocked"} else "",
        })
    except Exception as exc:
        state = read_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_STATE_REL)
        if isinstance(state, dict) and sc.text(state.get("status")) in {"queued", "running"}:
            state = {**state, "status": "failed", "error": str(exc), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            write_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_STATE_REL, state)
        sc.add_event(session_id, "koubo_storyboard.video_only_plan.execution_failed", {
            "task_id": task_id,
            "workspace_dir": str(workspace),
            "job_id": job_id,
            "mode": mode,
            "target_task_id": target_task_id,
            "target_asset_key": target_asset_key,
            "source_plan_hash": source_plan_hash,
            "error": str(exc),
        })
    finally:
        finish_execution_public_alias_job(sc.video_only_plan_execution_jobs, job_id, workspace, model_selection, ("video",), VIDEO_ONLY_PLAN_EXECUTION_STATE_REL, VIDEO_ONLY_PLAN_EXECUTION_RESULT_REL)


def preserve_execution_public_aliases(
    workspace: Path,
    model_selection: dict[str, Any] | None,
    kinds: tuple[str, ...],
    state_rel: str,
    result_rel: str,
) -> None:
    aliases = public_media_alias_selection(model_selection, kinds)
    if not aliases:
        return
    for rel_path in (state_rel, result_rel):
        path = workspace / rel_path
        payload = read_json(path)
        if not isinstance(payload, dict) or not payload:
            continue
        if any(_text_value(payload.get(key)) != value for key, value in aliases.items()):
            write_json(path, {**payload, **aliases})


def finish_execution_public_alias_job(
    jobs: dict[str, Any],
    job_id: str,
    workspace: Path,
    model_selection: dict[str, Any] | None,
    kinds: tuple[str, ...],
    state_rel: str,
    result_rel: str,
) -> None:
    try:
        preserve_execution_public_aliases(workspace, model_selection, kinds, state_rel, result_rel)
    finally:
        jobs.pop(job_id, None)


def register_tool_runner_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
