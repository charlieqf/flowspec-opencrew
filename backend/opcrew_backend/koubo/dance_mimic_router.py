from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from opcrew_backend.context import AppContext, now_ms
from opcrew_backend.workflow_modes import WORKFLOW_DANCE_MIMIC_V1, infer_openclip_workflow_mode

from .repository import OpenClipRepository
from .schemas import DanceMimicOneClickMoviePayload, DanceMimicRunPayload, DanceMimicTaskCreatePayload
from .koubo_storyboard.dance_mimic_stale import mark_dance_mimic_stale_items


OPENCLIP_SOURCE = "openclip-analysis"
OPENCLIP_GROUP_ID = "openclip-analysis"
TASK_META_REL = "SessionOutput/task_list/task_meta.json"
DANCE_MIMIC_RUN_STATE_REL = "SessionReport/dance_mimic_v1/run_state.json"
DANCE_MIMIC_ONE_CLICK_MOVIE_STATE_REL = "SessionReport/dance_mimic_v1/one_click_movie_state.json"
DANCE_MIMIC_STALE_MANIFEST_REL = "SessionReport/stale_manifest.json"
PRIVACY_GRID_MANIFEST_REL = "SessionOutput/reference/privacy_grid_manifest.json"
STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
STORYBOARD_SEED_REL = "SessionOutput/storyboard/storyboard_seed.json"
DANCE_MIMIC_TARGET = "dance_mimic_v1"
DANCE_MIMIC_ATTEMPT_FAMILY = "dance_mimic_v1_reference_toolchain"
OPENROUTER_PROVIDER = "openrouter"
OPENROUTER_SEEDANCE_MODEL = "bytedance/seedance-2.0"
TARGET_IDENTITY_IMAGE_REL_PREFIX = "SessionContext/Target_Identity_Image"
TARGET_IDENTITY_IMAGE_EXT_ORDER = (".png", ".jpg", ".jpeg", ".webp")
SUPPORTED_TARGET_IMAGE_EXTS = set(TARGET_IDENTITY_IMAGE_EXT_ORDER)
SUPPORTED_REFERENCE_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
SESSION_CONTEXT_REFERENCE_VIDEO_REL = "SessionContext/Video_Reference_Source.mp4"
DEFAULT_REFERENCE_PRIVACY_MODE = "face_mask_only"
DANCE_MIMIC_TARGET_IMAGE_UPLOAD_DIR_REL = "dance_mimic_v1/target_images"
DANCE_MIMIC_REFERENCE_VIDEO_UPLOAD_DIR_REL = "dance_mimic_v1/reference_videos"
TARGET_IMAGE_LIBRARY_RELS = ("SessionOutput/storyboard/assets/images", "SessionContext/Consistency")
REFERENCE_VIDEO_LIBRARY_RELS = ("SessionOutput/storyboard/assets/videos", "SessionOutput/reference")
TARGET_IMAGE_PERSON_KEYWORDS = (
    "人物",
    "数字人",
    "模特",
    "主持",
    "真人",
    "半身",
    "全身",
    "person",
    "human",
    "host",
    "presenter",
    "model",
    "portrait",
    "face",
    "man",
    "woman",
)

OPENCREW_REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = OPENCREW_REPO_ROOT / "backend"
BACKEND_VENV_PYTHON = BACKEND_ROOT / ".venv" / "bin" / "python"
DANCE_MIMIC_ROOT = OPENCREW_REPO_ROOT / "ToolLibrary" / "DanceMimic_V1"
ANALYSIS_V1_ROOT = OPENCREW_REPO_ROOT / "ToolLibrary" / "Analysis_V1"

DANCE_MIMIC_TOOL_SPECS = [
    {"id": "00", "name": "00_PrepareSessionVariables", "script": DANCE_MIMIC_ROOT / "00_PrepareSessionVariables.py", "timeout": 600},
    {"id": "01", "name": "01_ReferenceMediaDemux", "script": DANCE_MIMIC_ROOT / "01_ReferenceMediaDemux.py", "timeout": 1800},
    {"id": "02", "name": "02_ReferenceFaceMaskedVideoBuild", "script": DANCE_MIMIC_ROOT / "02_ReferenceFaceMaskedVideoBuild.py", "timeout": 7200},
    {"id": "03", "name": "03_StoryBoardStandardTaskBuild", "script": DANCE_MIMIC_ROOT / "03_StoryBoardStandardTaskBuild.py", "timeout": 900},
]

ONE_CLICK_MOVIE_TOOL_SPECS = [
    {"id": "05_01", "name": "05_01_VideoPlanGenerator", "script": ANALYSIS_V1_ROOT / "05_01_VideoPlanGenerator.py", "timeout": 900},
    {"id": "05_02", "name": "05_02_VideoPlanExecutor", "script": ANALYSIS_V1_ROOT / "05_02_VideoPlanExecutor.py", "timeout": 14400},
    {"id": "06_01", "name": "06_01_VideoPlanComposer", "script": ANALYSIS_V1_ROOT / "06_01_VideoPlanComposer.py", "timeout": 3600},
]

_RUN_LOCK = threading.RLock()
_ACTIVE_ATTEMPTS: set[int] = set()
_ONE_CLICK_MOVIE_LOCK = threading.RLock()
_ACTIVE_ONE_CLICK_MOVIE_RUNS: set[str] = set()


def text_value(value: Any) -> str:
    return str(value or "").strip()


def safe_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
        return parsed if parsed > 0 else fallback
    except Exception:
        return fallback


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_stdout_json(stdout: str) -> dict[str, Any]:
    text = str(stdout or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def repo_relative(path: Path | str) -> str:
    value = Path(path)
    try:
        return value.relative_to(OPENCREW_REPO_ROOT).as_posix()
    except Exception:
        return str(path)


def redact_log(value: str, limit: int = 12000) -> str:
    text = str(value or "")
    if len(text) > limit:
        text = text[-limit:]
    for pattern, replacement in (
        (r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,\"']+", r"\1[redacted]"),
        (r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,\"']+", r"\1[redacted]"),
        (r"(?i)(password\s*[:=]\s*)[^\s,\"']+", r"\1[redacted]"),
        (r"postgresql(?:\+psycopg2?|\+psycopg)?://[^\s,\"']+", "postgresql://[redacted]"),
        (r"sk-[A-Za-z0-9_\-]{8,}", "sk-[redacted]"),
    ):
        text = re.sub(pattern, replacement, text)
    return text


def python_executable() -> str:
    return str(BACKEND_VENV_PYTHON if BACKEND_VENV_PYTHON.exists() else Path(sys.executable))


def dance_mimic_tool_env(ctx: AppContext, task_id: int, session_id: int, attempt_id: int, step_id: str) -> dict[str, str]:
    env = dict(os.environ)
    paths = [str(OPENCREW_REPO_ROOT / "backend"), str(OPENCREW_REPO_ROOT), str(OPENCREW_REPO_ROOT.parent)]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONUNBUFFERED"] = "1"
    env["OPENCREW_WORKFLOW_ID"] = WORKFLOW_DANCE_MIMIC_V1
    env["OPENCREW_TASK_ID"] = str(task_id)
    env["OPENCREW_SESSION_ID"] = str(session_id)
    env["OPENCREW_ATTEMPT_ID"] = str(attempt_id)
    env["OPENCREW_STEP_ID"] = step_id
    env["OPENCREW_DATA_DIR"] = str(getattr(ctx, "data_dir", OPENCREW_REPO_ROOT))
    db_url = str(getattr(getattr(ctx, "config", None), "database_url", ""))
    env["OPENCREW_DATABASE_URL"] = db_url
    env["DATABASE_URL"] = db_url
    return env


def build_dance_mimic_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    repo = OpenClipRepository(ctx.engine)

    def workspace_for(task: dict[str, Any]) -> Path:
        workspace = text_value(task.get("workspace_dir"))
        if workspace:
            return Path(workspace)
        session = ctx.session_repo.get(int(task["session_id"]))
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return Path(str(session["workspace_dir"]))

    def get_task(task_id: int) -> dict[str, Any]:
        task = repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="OpenClip task not found")
        return task

    def ensure_dance_mimic_task(task: dict[str, Any]) -> None:
        mode = infer_openclip_workflow_mode(task, workspace=task.get("workspace_dir"))
        if mode == WORKFLOW_DANCE_MIMIC_V1:
            return
        raise HTTPException(
            status_code=400,
            detail={
                "code": "workflow_mode_not_dance_mimic_v1",
                "message": f"Task #{task.get('id')} uses workflow_mode={mode}; use the matching workflow surface.",
                "workflow_mode": mode,
                "target": WORKFLOW_DANCE_MIMIC_V1,
            },
        )

    def add_event(session_id: int, kind: str, payload: dict[str, Any], **event_fields: Any) -> None:
        service = getattr(ctx, "session_event_service", None)
        if service is not None:
            service.add_event(session_id, kind, payload, workflow_id=WORKFLOW_DANCE_MIMIC_V1, **event_fields)
            return
        session_repo = getattr(ctx, "session_repo", None)
        if session_repo is not None and hasattr(session_repo, "add_event"):
            session_repo.add_event(session_id, kind, json.dumps(payload, ensure_ascii=True), now_ms())

    def session_title(payload: DanceMimicTaskCreatePayload) -> str:
        title = payload.title.strip()
        if title:
            return title
        return "动作模拟任务"

    def default_session_task_title(task_id: int, session_id: int) -> str:
        return f"动作模拟Task #{task_id} - Session #{session_id}"

    def path_in_workspace(workspace: Path | None, path_value: str) -> Path:
        value = text_value(path_value)
        path = Path(value).expanduser()
        if path.is_absolute() or workspace is None:
            return path
        return workspace / path

    def path_for_task_config(workspace: Path | None, path: Path | None) -> str:
        if not path:
            return ""
        if workspace is not None:
            try:
                return path.resolve().relative_to(workspace.resolve()).as_posix()
            except Exception:
                pass
        return str(path)

    def validate_target_identity_image(path_value: str, workspace: Path | None = None) -> Path:
        target = path_in_workspace(workspace, path_value)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_target_identity_image_missing", "message": f"Target identity image not found: {target}"})
        if target.stat().st_size <= 0:
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_target_identity_image_empty", "message": f"Target identity image is empty: {target}"})
        if target.suffix.lower() not in SUPPORTED_TARGET_IMAGE_EXTS:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "dance_mimic_target_identity_image_unsupported",
                    "message": f"Target identity image must be one of: {', '.join(sorted(SUPPORTED_TARGET_IMAGE_EXTS))}",
                },
            )
        return target

    def validate_reference_video(path_value: str, workspace: Path | None = None) -> Path:
        source = path_in_workspace(workspace, path_value)
        if not source.exists() or not source.is_file():
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_reference_video_missing", "message": f"Reference video not found: {source}"})
        if source.stat().st_size <= 0:
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_reference_video_empty", "message": f"Reference video is empty: {source}"})
        if source.suffix.lower() not in SUPPORTED_REFERENCE_VIDEO_EXTS:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "dance_mimic_reference_video_unsupported",
                    "message": f"Reference video must be one of: {', '.join(sorted(SUPPORTED_REFERENCE_VIDEO_EXTS))}",
                },
            )
        return source

    def target_image_upload_root() -> Path:
        root = Path(ctx.data_dir) / DANCE_MIMIC_TARGET_IMAGE_UPLOAD_DIR_REL
        root.mkdir(parents=True, exist_ok=True)
        return root

    def reference_video_upload_root() -> Path:
        root = Path(ctx.data_dir) / DANCE_MIMIC_REFERENCE_VIDEO_UPLOAD_DIR_REL
        root.mkdir(parents=True, exist_ok=True)
        return root

    def allowed_target_image_preview_roots() -> list[Path]:
        roots = [target_image_upload_root(), ctx.workspace_store.sessions_root()]
        fixture_root = DANCE_MIMIC_ROOT / "test_fixtures"
        if fixture_root.exists():
            roots.append(fixture_root)
        return roots

    def allowed_reference_video_preview_roots() -> list[Path]:
        roots = [reference_video_upload_root(), ctx.workspace_store.sessions_root()]
        fixture_root = DANCE_MIMIC_ROOT / "test_fixtures"
        if fixture_root.exists():
            roots.append(fixture_root)
        return roots

    def ensure_previewable_target_image(path_value: str) -> Path:
        target = validate_target_identity_image(path_value)
        resolved = target.resolve()
        for root in allowed_target_image_preview_roots():
            try:
                resolved.relative_to(root.resolve())
                return target
            except Exception:
                continue
        raise HTTPException(status_code=403, detail={"code": "dance_mimic_target_image_preview_forbidden", "message": "Target image preview path is outside allowed DanceMimic and asset-library roots."})

    def ensure_previewable_reference_video(path_value: str) -> Path:
        source = validate_reference_video(path_value)
        resolved = source.resolve()
        for root in allowed_reference_video_preview_roots():
            try:
                resolved.relative_to(root.resolve())
                return source
            except Exception:
                continue
        raise HTTPException(status_code=403, detail={"code": "dance_mimic_reference_video_preview_forbidden", "message": "Reference video preview path is outside allowed DanceMimic and asset-library roots."})

    def target_image_media_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".jpg" or suffix == ".jpeg":
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        return "image/png"

    def reference_video_media_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".webm":
            return "video/webm"
        if suffix == ".mov":
            return "video/quicktime"
        return "video/mp4"

    def safe_upload_filename(filename: str, fallback: str = "target_identity.png", supported_exts: set[str] | None = None) -> str:
        allowed = supported_exts or SUPPORTED_TARGET_IMAGE_EXTS
        raw = Path(text_value(filename) or fallback).name.replace("\x00", "")
        raw = raw.strip(" .") or fallback
        suffix = Path(raw).suffix.lower()
        if suffix not in allowed:
            suffix = Path(fallback).suffix.lower()
            if suffix not in allowed:
                suffix = sorted(allowed)[0]
        stem = Path(raw).stem.strip(" .") or Path(fallback).stem
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)[:80].strip("._-") or Path(fallback).stem or "upload"
        return f"{stem}{suffix}"

    async def save_upload_file_to_session_context(
        file: UploadFile,
        *,
        workspace: Path,
        fallback: str,
        supported_exts: set[str],
        unsupported_code: str,
        empty_code: str,
        label: str,
        target_rel: str | None = None,
        target_rel_prefix: str | None = None,
    ) -> Path:
        original_suffix = Path(text_value(file.filename or "")).suffix.lower()
        if original_suffix and original_suffix not in supported_exts:
            raise HTTPException(status_code=400, detail={"code": unsupported_code, "message": f"{label} must be one of: {', '.join(sorted(supported_exts))}"})
        filename = safe_upload_filename(file.filename or fallback, fallback, supported_exts)
        suffix = Path(filename).suffix.lower()
        if suffix not in supported_exts:
            raise HTTPException(status_code=400, detail={"code": unsupported_code, "message": f"{label} must be one of: {', '.join(sorted(supported_exts))}"})
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail={"code": empty_code, "message": f"Uploaded {label} is empty."})
        if target_rel_prefix:
            target = workspace / f"{target_rel_prefix}{suffix}"
            for candidate_suffix in supported_exts:
                candidate = workspace / f"{target_rel_prefix}{candidate_suffix}"
                if candidate != target and candidate.exists():
                    candidate.unlink()
        elif target_rel:
            target = workspace / target_rel
        else:
            raise HTTPException(status_code=500, detail={"code": "dance_mimic_session_context_target_missing", "message": "SessionContext upload target is not configured."})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def unique_upload_path(path: Path, conflict_code: str = "dance_mimic_upload_name_conflict") -> Path:
        if not path.exists():
            return path
        for index in range(2, 1000):
            candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise HTTPException(status_code=409, detail={"code": conflict_code, "message": "Could not choose a unique upload filename."})

    def image_sidecar_text(path: Path) -> str:
        sidecar = path.with_suffix(".json")
        if not sidecar.exists() or not sidecar.is_file():
            return ""
        payload = read_json(sidecar)
        origin = payload.get("origin") if isinstance(payload.get("origin"), dict) else {}
        pieces = [
            text_value(payload.get("prompt")),
            text_value(payload.get("effective_prompt")),
            text_value(origin.get("prompt")),
            text_value(origin.get("effective_prompt")),
            text_value(origin.get("tool")),
        ]
        return "\n".join(item for item in pieces if item)

    def is_ai_generated_target_image(path: Path) -> bool:
        sidecar_text = image_sidecar_text(path).lower()
        return "agent_generated" in path.name.lower() or "upload_asset_library_agent" in sidecar_text

    def has_person_hint(path: Path) -> bool:
        haystack = f"{path.name}\n{image_sidecar_text(path)}".lower()
        return any(keyword.lower() in haystack for keyword in TARGET_IMAGE_PERSON_KEYWORDS)

    def task_context_for_path(path: Path, workspace_rows: list[tuple[Path, dict[str, Any]]]) -> tuple[dict[str, Any], str]:
        resolved = path.resolve()
        for workspace, row in workspace_rows:
            try:
                rel_path = resolved.relative_to(workspace.resolve()).as_posix()
                return row, rel_path
            except Exception:
                continue
        return {}, ""

    def target_image_preview_url(path: Path) -> str:
        return f"/api/dance-mimic-v1/target-images/preview?path={urllib.parse.quote(str(path), safe='')}"

    def target_image_candidate(path: Path, source: str, workspace_rows: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
        task_row, workspace_rel_path = task_context_for_path(path, workspace_rows)
        ai_generated = is_ai_generated_target_image(path)
        person_hint = has_person_hint(path)
        stat = path.stat()
        score = 0
        if source == "uploaded":
            score += 500
        if ai_generated:
            score += 200
        if person_hint:
            score += 100
        if "host" in path.name.lower() or "target" in path.name.lower():
            score += 30
        label_prefix = "AI person" if ai_generated and person_hint else ("AI image" if ai_generated else "Image")
        task_id = int(task_row.get("id") or 0) if task_row else 0
        session_id = int(task_row.get("session_id") or 0) if task_row else 0
        return {
            "id": str(path),
            "path": str(path),
            "absolute_path": str(path),
            "filename": path.name,
            "label": f"{label_prefix} - {path.name}",
            "source": source,
            "task_id": task_id or None,
            "session_id": session_id or None,
            "task_title": text_value(task_row.get("title")) if task_row else "",
            "workspace_rel_path": workspace_rel_path,
            "preview_url": target_image_preview_url(path),
            "is_ai_generated": ai_generated,
            "person_hint": person_hint,
            "size_bytes": int(stat.st_size),
            "updated_at": int(stat.st_mtime * 1000),
            "score": score,
        }

    def reference_video_preview_url(path: Path) -> str:
        return f"/api/dance-mimic-v1/reference-videos/preview?path={urllib.parse.quote(str(path), safe='')}"

    def reference_video_candidate(path: Path, source: str, workspace_rows: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
        task_row, workspace_rel_path = task_context_for_path(path, workspace_rows)
        stat = path.stat()
        score = 0
        if source == "uploaded":
            score += 500
        if source == "fixture":
            score += 300
        if source == "asset_library":
            score += 100
        if "dance" in path.name.lower() or "reference" in path.name.lower():
            score += 20
        task_id = int(task_row.get("id") or 0) if task_row else 0
        session_id = int(task_row.get("session_id") or 0) if task_row else 0
        return {
            "id": str(path),
            "path": str(path),
            "absolute_path": str(path),
            "filename": path.name,
            "label": f"参考视频 - {path.name}",
            "source": source,
            "task_id": task_id or None,
            "session_id": session_id or None,
            "task_title": text_value(task_row.get("title")) if task_row else "",
            "workspace_rel_path": workspace_rel_path,
            "preview_url": reference_video_preview_url(path),
            "size_bytes": int(stat.st_size),
            "updated_at": int(stat.st_mtime * 1000),
            "score": score,
        }

    def workspace_rows_for_assets() -> list[tuple[Path, dict[str, Any]]]:
        workspace_rows: list[tuple[Path, dict[str, Any]]] = []
        seen_workspaces: set[str] = set()
        for row in repo.list_task_summaries():
            workspace = Path(text_value(row.get("workspace_dir"))).expanduser()
            if workspace.exists():
                key = str(workspace.resolve())
                if key not in seen_workspaces:
                    workspace_rows.append((workspace, row))
                    seen_workspaces.add(key)
        sessions_root = ctx.workspace_store.sessions_root()
        if sessions_root.exists():
            for workspace in sorted(sessions_root.glob("*/workspace")):
                if not workspace.exists():
                    continue
                key = str(workspace.resolve())
                if key not in seen_workspaces:
                    workspace_rows.append((workspace, {}))
                    seen_workspaces.add(key)
        return workspace_rows

    def collect_target_image_candidates(limit: int, ai_person_only: bool = True) -> list[dict[str, Any]]:
        workspace_rows = workspace_rows_for_assets()

        candidates: list[dict[str, Any]] = []
        seen_paths: set[str] = set()

        def add_path(path: Path, source: str) -> None:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_TARGET_IMAGE_EXTS or path.stat().st_size <= 0:
                return
            key = str(path.resolve())
            if key in seen_paths:
                return
            item = target_image_candidate(path, source, workspace_rows)
            if ai_person_only and source != "uploaded" and not (item["is_ai_generated"] and item["person_hint"]):
                return
            seen_paths.add(key)
            candidates.append(item)

        upload_root = target_image_upload_root()
        for path in sorted(upload_root.glob("*")):
            add_path(path, "uploaded")

        for workspace, _row in workspace_rows:
            for rel_dir in TARGET_IMAGE_LIBRARY_RELS:
                root = workspace / rel_dir
                if not root.exists():
                    continue
                for path in sorted(root.rglob("*")):
                    add_path(path, "asset_library")

        candidates.sort(key=lambda item: (int(item.get("score") or 0), int(item.get("updated_at") or 0)), reverse=True)
        return candidates[:limit]

    def collect_reference_video_candidates(limit: int) -> list[dict[str, Any]]:
        workspace_rows = workspace_rows_for_assets()
        candidates: list[dict[str, Any]] = []
        seen_paths: set[str] = set()

        def add_path(path: Path, source: str) -> None:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_REFERENCE_VIDEO_EXTS or path.stat().st_size <= 0:
                return
            key = str(path.resolve())
            if key in seen_paths:
                return
            seen_paths.add(key)
            candidates.append(reference_video_candidate(path, source, workspace_rows))

        upload_root = reference_video_upload_root()
        for path in sorted(upload_root.glob("*")):
            add_path(path, "uploaded")

        fixture_root = DANCE_MIMIC_ROOT / "test_fixtures"
        if fixture_root.exists():
            for path in sorted(fixture_root.glob("*")):
                add_path(path, "fixture")

        for workspace, _row in workspace_rows:
            for rel_dir in REFERENCE_VIDEO_LIBRARY_RELS:
                root = workspace / rel_dir
                if not root.exists():
                    continue
                for path in sorted(root.rglob("*")):
                    add_path(path, "asset_library")

        candidates.sort(key=lambda item: (int(item.get("score") or 0), int(item.get("updated_at") or 0)), reverse=True)
        return candidates[:limit]

    def dance_mimic_meta(meta: dict[str, Any]) -> dict[str, Any]:
        value = meta.get("dance_mimic")
        return value if isinstance(value, dict) else {}

    def target_identity_image_from_meta(meta: dict[str, Any]) -> str:
        dance_meta = dance_mimic_meta(meta)
        return text_value(dance_meta.get("target_identity_image_path") or meta.get("target_identity_image_path"))

    def reference_privacy_mode_from_meta(meta: dict[str, Any]) -> str:
        dance_meta = dance_mimic_meta(meta)
        return text_value(dance_meta.get("reference_privacy_mode") or meta.get("reference_privacy_mode") or DEFAULT_REFERENCE_PRIVACY_MODE)

    def privacy_grid_flag_from_meta(meta: dict[str, Any], field: str) -> bool:
        dance_meta = dance_mimic_meta(meta)
        value = dance_meta.get(field, meta.get(field, True))
        return bool(value)

    def effective_grid_scope(reference_enabled: bool, target_enabled: bool, privacy_mode: str) -> str:
        if text_value(privacy_mode) != "red_grid_guide":
            return "none"
        if reference_enabled and target_enabled:
            return "both"
        if reference_enabled:
            return "reference_video"
        if target_enabled:
            return "target_identity"
        return "none"

    def payload_field_was_set(payload: DanceMimicRunPayload, field: str) -> bool:
        fields_set = getattr(payload, "model_fields_set", None)
        if fields_set is None:
            fields_set = getattr(payload, "__fields_set__", set())
        return field in fields_set

    def run_payload_from_meta(meta: dict[str, Any], payload: DanceMimicRunPayload | None = None) -> DanceMimicRunPayload:
        dance_meta = dance_mimic_meta(meta)
        if payload is None:
            return DanceMimicRunPayload(
                target_video_seconds=safe_float(dance_meta.get("target_video_seconds"), 8.0),
                minimum_video_seconds=safe_float(dance_meta.get("minimum_video_seconds"), 4.0),
                face_detections_manifest=text_value(dance_meta.get("face_detections_manifest")),
                reference_privacy_mode=reference_privacy_mode_from_meta(meta),
                apply_privacy_grid_to_reference_video=privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_reference_video"),
                apply_privacy_grid_to_target_identity_image=privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_target_identity_image"),
                block_on_face_not_detected=bool(dance_meta.get("block_on_face_not_detected")),
                force=False,
            )
        return DanceMimicRunPayload(
            target_video_seconds=safe_float(payload.target_video_seconds if payload_field_was_set(payload, "target_video_seconds") else dance_meta.get("target_video_seconds"), 8.0),
            minimum_video_seconds=safe_float(payload.minimum_video_seconds if payload_field_was_set(payload, "minimum_video_seconds") else dance_meta.get("minimum_video_seconds"), 4.0),
            face_detections_manifest=text_value(payload.face_detections_manifest if payload_field_was_set(payload, "face_detections_manifest") else dance_meta.get("face_detections_manifest")),
            reference_privacy_mode=text_value(payload.reference_privacy_mode) or reference_privacy_mode_from_meta(meta),
            apply_privacy_grid_to_reference_video=bool(payload.apply_privacy_grid_to_reference_video if payload_field_was_set(payload, "apply_privacy_grid_to_reference_video") else privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_reference_video")),
            apply_privacy_grid_to_target_identity_image=bool(payload.apply_privacy_grid_to_target_identity_image if payload_field_was_set(payload, "apply_privacy_grid_to_target_identity_image") else privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_target_identity_image")),
            block_on_face_not_detected=bool(payload.block_on_face_not_detected if payload_field_was_set(payload, "block_on_face_not_detected") else dance_meta.get("block_on_face_not_detected")),
            force=bool(payload.force),
        )

    def json_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(value or "{}"))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def is_legacy_session_input_path(path_value: str) -> bool:
        return "SessionInput/dance_mimic/" in text_value(path_value).replace("\\", "/")

    def copy_to_canonical_session_context(source: Path, target: Path) -> bool:
        if not source.exists() or not source.is_file() or source.stat().st_size <= 0:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            target.write_bytes(source.read_bytes())
        return True

    def normalize_legacy_session_context_inputs(task: dict[str, Any]) -> dict[str, Any]:
        workspace = workspace_for(task)
        meta_path = workspace / TASK_META_REL
        meta = read_json(meta_path)
        dance_meta = dict(dance_mimic_meta(meta))
        quick_config = json_dict(task.get("storyboard_quick_config_json"))
        changed = False

        reference_value = text_value(task.get("reference_video_path") or dance_meta.get("reference_video_path") or meta.get("reference_video_path"))
        if is_legacy_session_input_path(reference_value):
            source = path_in_workspace(workspace, reference_value)
            target = workspace / SESSION_CONTEXT_REFERENCE_VIDEO_REL
            if copy_to_canonical_session_context(source, target):
                canonical = SESSION_CONTEXT_REFERENCE_VIDEO_REL
                task["reference_video_path"] = canonical
                meta["reference_video_path"] = canonical
                dance_meta["reference_video_path"] = canonical
                quick_config["reference_video_path"] = canonical
                changed = True

        target_value = text_value(dance_meta.get("target_identity_image_path") or meta.get("target_identity_image_path"))
        if is_legacy_session_input_path(target_value):
            source = path_in_workspace(workspace, target_value)
            suffix = source.suffix.lower()
            if suffix in SUPPORTED_TARGET_IMAGE_EXTS:
                target_rel = f"{TARGET_IDENTITY_IMAGE_REL_PREFIX}{suffix}"
                target = workspace / target_rel
                if copy_to_canonical_session_context(source, target):
                    for candidate_suffix in TARGET_IDENTITY_IMAGE_EXT_ORDER:
                        candidate = workspace / f"{TARGET_IDENTITY_IMAGE_REL_PREFIX}{candidate_suffix}"
                        if candidate != target and candidate.exists():
                            candidate.unlink()
                    meta["target_identity_image_path"] = target_rel
                    dance_meta["target_identity_image_path"] = target_rel
                    quick_config["target_identity_image_path"] = target_rel
                    changed = True

        if changed:
            updated = now_ms()
            meta["dance_mimic"] = dance_meta
            meta["updated_at"] = updated
            repo.update_task(
                int(task["id"]),
                reference_video_path=text_value(task.get("reference_video_path") or meta.get("reference_video_path")),
                storyboard_quick_config_json=json.dumps(quick_config, ensure_ascii=False, sort_keys=True),
                updated_at=updated,
            )
            write_json(meta_path, meta)
            task = get_task(int(task["id"]))
        return task

    def normalize_run_payload(value: DanceMimicRunPayload | DanceMimicTaskCreatePayload) -> DanceMimicRunPayload:
        return DanceMimicRunPayload(
            target_video_seconds=safe_float(value.target_video_seconds, 8.0),
            minimum_video_seconds=safe_float(value.minimum_video_seconds, 4.0),
            face_detections_manifest=text_value(value.face_detections_manifest),
            reference_privacy_mode=text_value(value.reference_privacy_mode) or DEFAULT_REFERENCE_PRIVACY_MODE,
            apply_privacy_grid_to_reference_video=bool(value.apply_privacy_grid_to_reference_video),
            apply_privacy_grid_to_target_identity_image=bool(value.apply_privacy_grid_to_target_identity_image),
            block_on_face_not_detected=bool(value.block_on_face_not_detected),
            force=bool(getattr(value, "force", False)),
        )

    def step_payload(spec: dict[str, Any], status: str = "pending") -> dict[str, Any]:
        return {
            "id": str(spec["id"]),
            "name": str(spec["name"]),
            "status": status,
            "script": repo_relative(spec["script"]),
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "returncode": None,
            "tool_status": "",
            "message": "",
            "result_path": "",
            "stdout_tail": "",
            "stderr_tail": "",
            "argv": [],
        }

    def compile_plan(payload: DanceMimicRunPayload) -> dict[str, Any]:
        return {
            "target": DANCE_MIMIC_TARGET,
            "attempt_family": DANCE_MIMIC_ATTEMPT_FAMILY,
            "provider": OPENROUTER_PROVIDER,
            "model": OPENROUTER_SEEDANCE_MODEL,
            "steps": [step_payload(spec) for spec in DANCE_MIMIC_TOOL_SPECS],
            "options": {
                "target_video_seconds": safe_float(payload.target_video_seconds, 8.0),
                "minimum_video_seconds": safe_float(payload.minimum_video_seconds, 4.0),
                "face_detections_manifest": text_value(payload.face_detections_manifest),
                "reference_privacy_mode": text_value(payload.reference_privacy_mode) or DEFAULT_REFERENCE_PRIVACY_MODE,
                "apply_privacy_grid_to_reference_video": bool(payload.apply_privacy_grid_to_reference_video),
                "apply_privacy_grid_to_target_identity_image": bool(payload.apply_privacy_grid_to_target_identity_image),
                "effective_grid_scope": effective_grid_scope(bool(payload.apply_privacy_grid_to_reference_video), bool(payload.apply_privacy_grid_to_target_identity_image), text_value(payload.reference_privacy_mode)),
                "block_on_face_not_detected": bool(payload.block_on_face_not_detected),
                "force": bool(payload.force),
            },
        }

    def run_state_path(workspace: Path, attempt_id: int) -> Path:
        return workspace / "SessionReport" / "dance_mimic_v1" / f"attempt_{attempt_id}" / "run_state.json"

    def write_run_state(workspace: Path, attempt_id: int, state: dict[str, Any]) -> dict[str, Any]:
        state["updated_at"] = now_ms()
        write_json(run_state_path(workspace, attempt_id), state)
        write_json(workspace / DANCE_MIMIC_RUN_STATE_REL, state)
        return state

    def load_run_state(workspace: Path, attempt_id: int) -> dict[str, Any]:
        state = read_json(run_state_path(workspace, attempt_id))
        return state or read_json(workspace / DANCE_MIMIC_RUN_STATE_REL)

    def result_manifest(workspace: Path, task_id: int, attempt_id: int, steps: list[dict[str, Any]]) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        manifest_entries = [
            ("SessionContext/Variables.json", "session_context"),
            ("SessionContext/Video_Reference_Source.mp4", "source_reference"),
            ("SessionOutput/reference/reference_media_manifest.json", "reference_manifest"),
            ("SessionOutput/reference/segments/reference_segments_manifest.json", "reference_segments"),
            ("SessionOutput/storyboard/srt_storyboard.json", "storyboard"),
            ("SessionOutput/storyboard/storyboard_seed.json", "storyboard_seed"),
            (DANCE_MIMIC_STALE_MANIFEST_REL, "stale_manifest"),
            (DANCE_MIMIC_RUN_STATE_REL, "run_state"),
        ]
        for suffix in SUPPORTED_TARGET_IMAGE_EXTS:
            rel_path = f"{TARGET_IDENTITY_IMAGE_REL_PREFIX}{suffix}"
            if (workspace / rel_path).is_file():
                manifest_entries.insert(2, (rel_path, "target_identity_image"))
                break
        for rel, kind in manifest_entries:
            path = workspace / rel
            if path.is_file():
                files.append({"path": rel, "kind": kind, "size": int(path.stat().st_size), "downloadable": True})
        return {
            "schema_version": "dance_mimic_v1_result_manifest_0.1",
            "task_id": task_id,
            "attempt_id": attempt_id,
            "target": DANCE_MIMIC_TARGET,
            "attempt_family": DANCE_MIMIC_ATTEMPT_FAMILY,
            "finished_at": now_ms(),
            "steps": steps,
            "tool_outputs": [{"tool_id": "dance_mimic_v1_reference_toolchain", "files": files}],
        }

    def stale_summary(workspace: Path) -> dict[str, Any]:
        manifest = read_json(workspace / DANCE_MIMIC_STALE_MANIFEST_REL)
        items = manifest.get("items") if isinstance(manifest.get("items"), dict) else {}
        events = manifest.get("events") if isinstance(manifest.get("events"), list) else []
        return {
            "path": DANCE_MIMIC_STALE_MANIFEST_REL,
            "exists": bool((workspace / DANCE_MIMIC_STALE_MANIFEST_REL).is_file()),
            "schema_version": text_value(manifest.get("schema_version") or "dance_mimic_v1_stale_manifest_0.1"),
            "workflow_id": text_value(manifest.get("workflow_id") or WORKFLOW_DANCE_MIMIC_V1),
            "items": items,
            "events": events[-20:],
            "active_count": len(items),
            "updated_at": manifest.get("updated_at"),
        }

    def privacy_grid_step_status(latest_run: dict[str, Any] | None) -> tuple[str, str]:
        for step in (latest_run or {}).get("steps") or []:
            if text_value(step.get("id")) != "02":
                continue
            status = text_value(step.get("status"))
            message = text_value(step.get("error") or step.get("summary") or step.get("message"))
            if status in {"blocked", "failed", "cancelled"}:
                return "blocked", message
            if status in {"queued", "running", "pending"}:
                return "pending", message
        return "pending", ""

    def safe_privacy_preview_path(workspace: Path, rel_path: str) -> Path | None:
        value = text_value(rel_path)
        if not value or Path(value).is_absolute():
            return None
        root = workspace.resolve()
        target = (workspace / value).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return target if target.is_file() and target.stat().st_size > 0 else None

    def privacy_preview_url(task_id: int, kind: str, sha256: str) -> str:
        version = text_value(sha256)[:16]
        suffix = f"?v={urllib.parse.quote(version, safe='')}" if version else ""
        return f"/api/dance-mimic-v1/tasks/{task_id}/privacy-grid-preview/{kind}{suffix}"

    def privacy_grid_preview_summary(task_id: int, workspace: Path, meta: dict[str, Any], latest_run: dict[str, Any] | None) -> dict[str, Any]:
        mode = reference_privacy_mode_from_meta(meta)
        scope = effective_grid_scope(
            privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_reference_video"),
            privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_target_identity_image"),
            mode,
        )
        base = {"status": "not_applicable", "effective_grid_scope": scope, "reference_video": {}, "target_identity": {}, "message": ""}
        if mode != "red_grid_guide":
            return base
        stale = stale_summary(workspace)
        stale_item = (stale.get("items") or {}).get("02_reference_face_masked_video_build") or {}
        if text_value(stale_item.get("status")) == "stale":
            return {**base, "status": "stale", "message": "隐私网格配置已变更，需要重新运行动作模拟。"}
        manifest_path = workspace / PRIVACY_GRID_MANIFEST_REL
        if not manifest_path.is_file():
            status, message = privacy_grid_step_status(latest_run)
            return {**base, "status": status, "message": message or "步骤 02 完成后可查看隐私网格预览。"}
        manifest = read_json(manifest_path)
        expected_reference = privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_reference_video")
        expected_target = privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_target_identity_image")
        if (
            text_value(manifest.get("mode")) != "red_grid_guide"
            or bool(manifest.get("apply_to_reference_video")) != expected_reference
            or bool(manifest.get("apply_to_target_identity_image")) != expected_target
        ):
            return {**base, "status": "stale", "message": "隐私网格预览与当前配置不一致，需要重新运行动作模拟。"}
        reference = manifest.get("reference_video") if isinstance(manifest.get("reference_video"), dict) else {}
        target = manifest.get("target_identity") if isinstance(manifest.get("target_identity"), dict) else {}
        reference_preview = reference.get("preview") if isinstance(reference.get("preview"), dict) else {}
        reference_path = safe_privacy_preview_path(workspace, text_value(reference_preview.get("path"))) if expected_reference else None
        target_path = safe_privacy_preview_path(workspace, text_value(target.get("provider_path"))) if expected_target else None
        reference_payload = {
            "grid_applied": expected_reference,
            "preview_url": privacy_preview_url(task_id, "reference", text_value(reference_preview.get("sha256"))) if reference_path else "",
            "preview_timestamp_seconds": reference_preview.get("timestamp_seconds"),
        }
        target_payload = {
            "grid_applied": expected_target,
            "preview_url": privacy_preview_url(task_id, "target", text_value(target.get("provider_sha256"))) if target_path else "",
        }
        if (expected_reference and not reference_path) or (expected_target and not target_path):
            return {
                **base,
                "status": "pending",
                "reference_video": reference_payload,
                "target_identity": target_payload,
                "message": "当前产物没有预览图片，需要重新运行动作模拟。",
            }
        return {
            **base,
            "status": "ready",
            "effective_grid_scope": text_value(manifest.get("effective_grid_scope") or scope),
            "reference_video": reference_payload,
            "target_identity": target_payload,
            "updated_at": manifest.get("created_at"),
        }

    def privacy_grid_preview_file(task_id: int, kind: str) -> tuple[Path, str]:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        task = normalize_legacy_session_context_inputs(task)
        workspace = workspace_for(task)
        meta = read_json(workspace / TASK_META_REL)
        if reference_privacy_mode_from_meta(meta) != "red_grid_guide":
            raise HTTPException(status_code=404, detail={"code": "privacy_grid_preview_not_applicable", "message": "This task does not use privacy grid mode."})
        stale_item = (stale_summary(workspace).get("items") or {}).get("02_reference_face_masked_video_build") or {}
        if text_value(stale_item.get("status")) == "stale":
            raise HTTPException(status_code=409, detail={"code": "privacy_grid_preview_stale", "message": "Privacy grid preview is stale; rerun DanceMimic preprocessing."})
        manifest_path = workspace / PRIVACY_GRID_MANIFEST_REL
        if not manifest_path.is_file():
            raise HTTPException(status_code=404, detail={"code": "privacy_grid_preview_missing", "message": "Privacy grid preview is not available."})
        manifest = read_json(manifest_path)
        if text_value(manifest.get("mode")) != "red_grid_guide":
            raise HTTPException(status_code=409, detail={"code": "privacy_grid_preview_manifest_invalid", "message": "Privacy grid preview manifest mode is invalid."})
        expected_reference = privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_reference_video")
        expected_target = privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_target_identity_image")
        if (
            bool(manifest.get("apply_to_reference_video")) != expected_reference
            or bool(manifest.get("apply_to_target_identity_image")) != expected_target
        ):
            raise HTTPException(status_code=409, detail={"code": "privacy_grid_preview_config_mismatch", "message": "Privacy grid preview does not match the current task configuration."})
        if kind == "reference":
            reference = manifest.get("reference_video") if isinstance(manifest.get("reference_video"), dict) else {}
            preview = reference.get("preview") if isinstance(reference.get("preview"), dict) else {}
            applied = bool(reference.get("grid_applied"))
            rel_path = text_value(preview.get("path"))
            expected_sha256 = text_value(preview.get("sha256"))
        elif kind == "target":
            target = manifest.get("target_identity") if isinstance(manifest.get("target_identity"), dict) else {}
            applied = bool(target.get("grid_applied"))
            rel_path = text_value(target.get("provider_path"))
            expected_sha256 = text_value(target.get("provider_sha256"))
        else:
            raise HTTPException(status_code=404, detail={"code": "privacy_grid_preview_kind_invalid", "message": "Unknown privacy grid preview type."})
        if not applied:
            raise HTTPException(status_code=404, detail={"code": "privacy_grid_preview_not_applied", "message": "Privacy grid is not applied to this input."})
        target_path = safe_privacy_preview_path(workspace, rel_path)
        if target_path is None:
            raise HTTPException(status_code=404, detail={"code": "privacy_grid_preview_file_missing", "message": "Privacy grid preview file is missing."})
        digest = hashlib.sha256()
        with target_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_sha256 = digest.hexdigest()
        if not expected_sha256 or actual_sha256 != expected_sha256:
            raise HTTPException(status_code=409, detail={"code": "privacy_grid_preview_hash_mismatch", "message": "Privacy grid preview file does not match its manifest."})
        return target_path, actual_sha256

    def command_for_step(spec: dict[str, Any], task: dict[str, Any], attempt_id: int, payload: DanceMimicRunPayload) -> list[str]:
        task_id = int(task["id"])
        session_id = int(task["session_id"])
        workspace = workspace_for(task)
        command = [
            python_executable(),
            str(spec["script"]),
            "--workspace",
            str(workspace),
            "--workflow-id",
            WORKFLOW_DANCE_MIMIC_V1,
            "--task-id",
            str(task_id),
            "--session-id",
            str(session_id),
            "--attempt-id",
            str(attempt_id),
            "--print-json",
        ]
        if payload.force:
            command.append("--force")
        if spec["id"] == "00":
            command.extend(["--source-video-path", text_value(task.get("reference_video_path"))])
            meta = read_json(workspace / TASK_META_REL)
            target_identity = target_identity_image_from_meta(meta)
            if target_identity:
                command.extend(["--target-identity-image-path", target_identity])
            reference_privacy_mode = text_value(payload.reference_privacy_mode) or reference_privacy_mode_from_meta(meta)
            if reference_privacy_mode:
                command.extend(["--reference-privacy-mode", reference_privacy_mode])
            command.append("--apply-privacy-grid-to-reference-video" if payload.apply_privacy_grid_to_reference_video else "--no-apply-privacy-grid-to-reference-video")
            command.append("--apply-privacy-grid-to-target-identity-image" if payload.apply_privacy_grid_to_target_identity_image else "--no-apply-privacy-grid-to-target-identity-image")
        if spec["id"] == "02":
            command.extend([
                "--target-video-seconds",
                str(safe_float(payload.target_video_seconds, 8.0)),
                "--minimum-video-seconds",
                str(safe_float(payload.minimum_video_seconds, 4.0)),
            ])
            if text_value(payload.face_detections_manifest):
                command.extend(["--face-detections-manifest", text_value(payload.face_detections_manifest)])
            if payload.block_on_face_not_detected:
                command.append("--block-on-face-not-detected")
        return command

    def run_tool_step(
        *,
        task: dict[str, Any],
        attempt_id: int,
        payload: DanceMimicRunPayload,
        spec: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[str, str]:
        task_id = int(task["id"])
        session_id = int(task["session_id"])
        workspace = workspace_for(task)
        step_id = str(spec["id"])
        step = next(item for item in state["steps"] if item["id"] == step_id)
        step_started_at = now_ms()
        step.update({"status": "running", "started_at": step_started_at, "finished_at": None, "duration_seconds": None, "argv": command_for_step(spec, task, attempt_id, payload)})
        state["status"] = "running"
        state["current_step_id"] = step_id
        write_run_state(workspace, attempt_id, state)
        add_event(session_id, "dance_mimic_v1.step.started", {"task_id": task_id, "attempt_id": attempt_id, "step_id": step_id}, task_id=task_id, attempt_id=attempt_id, step_id=step_id)
        completed = subprocess.run(
            step["argv"],
            cwd=str(OPENCREW_REPO_ROOT),
            env=dance_mimic_tool_env(ctx, task_id, session_id, attempt_id, step_id),
            capture_output=True,
            text=True,
            check=False,
            timeout=int(spec.get("timeout") or 3600),
        )
        parsed = parse_stdout_json(completed.stdout)
        tool_status = text_value(parsed.get("status"))
        result_rel = text_value((parsed.get("outputs") or {}).get("result_path") if isinstance(parsed.get("outputs"), dict) else "")
        if not result_rel:
            result_rel = f"S{int(step_id) + 1}_{spec['name']}/Report/Result.json" if step_id.isdigit() else ""
        error_payload = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
        message = text_value(parsed.get("message") or error_payload.get("message"))
        if not message:
            reasons = parsed.get("blocked_reasons") if isinstance(parsed.get("blocked_reasons"), list) else []
            message = "; ".join(text_value(item.get("message") or item.get("code")) for item in reasons if isinstance(item, dict))
        if completed.returncode == 0 and tool_status not in {"failed", "blocked"}:
            step_status = "completed"
        elif completed.returncode == 2 or tool_status == "blocked":
            step_status = "blocked"
        else:
            step_status = "failed"
        step_finished_at = now_ms()
        step.update({
            "status": step_status,
            "finished_at": step_finished_at,
            "duration_seconds": round(max(0, step_finished_at - int(step.get("started_at") or step_started_at)) / 1000, 3),
            "returncode": completed.returncode,
            "tool_status": tool_status,
            "message": message,
            "result_path": result_rel,
            "stdout_tail": redact_log(completed.stdout),
            "stderr_tail": redact_log(completed.stderr),
        })
        write_run_state(workspace, attempt_id, state)
        add_event(session_id, f"dance_mimic_v1.step.{step_status}", {"task_id": task_id, "attempt_id": attempt_id, "step_id": step_id, "message": message}, task_id=task_id, attempt_id=attempt_id, step_id=step_id)
        return step_status, message or f"{step_id} {step_status}"

    def run_attempt_background(task_id: int, attempt_id: int, payload: DanceMimicRunPayload) -> None:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        workspace = workspace_for(task)
        session_id = int(task["session_id"])
        started_at = now_ms()
        final_status = "completed"
        final_summary = "DanceMimic_V1 reference toolchain completed"
        try:
            repo.update_attempt(attempt_id, status="running", started_at=started_at)
            repo.update_task(task_id, status="running", latest_attempt_id=attempt_id, run_model_provider=OPENROUTER_PROVIDER, run_model_id=OPENROUTER_SEEDANCE_MODEL, updated_at=started_at)
            ctx.session_repo.update(session_id, status="running", started_at=started_at, finished_at=None, updated_at=started_at)
            state = load_run_state(workspace, attempt_id)
            state.update({"status": "running", "started_at": started_at, "current_step_id": None})
            write_run_state(workspace, attempt_id, state)
            add_event(session_id, "dance_mimic_v1.attempt.started", {"task_id": task_id, "attempt_id": attempt_id}, task_id=task_id, attempt_id=attempt_id)
            for spec in DANCE_MIMIC_TOOL_SPECS:
                step_status, message = run_tool_step(task=task, attempt_id=attempt_id, payload=payload, spec=spec, state=state)
                if step_status == "blocked":
                    final_status = "blocked"
                    final_summary = message
                    break
                if step_status != "completed":
                    final_status = "failed"
                    final_summary = message
                    break
            finished_at = now_ms()
            duration_seconds = round(max(0, finished_at - started_at) / 1000, 3)
            state["status"] = final_status
            state["current_step_id"] = None
            state["finished_at"] = finished_at
            state["duration_seconds"] = duration_seconds
            state["summary"] = final_summary
            manifest = result_manifest(workspace, task_id, attempt_id, state["steps"])
            state["result_manifest"] = manifest
            write_run_state(workspace, attempt_id, state)
            repo.update_attempt(attempt_id, status=final_status, summary=final_summary[:4000], result_manifest_json=json.dumps(manifest, ensure_ascii=False), finished_at=finished_at)
            repo.update_task(task_id, status=final_status, latest_attempt_id=attempt_id, updated_at=finished_at)
            ctx.session_repo.update(session_id, status="waiting_input" if final_status == "completed" else final_status, finished_at=finished_at, updated_at=finished_at)
            add_event(session_id, f"dance_mimic_v1.attempt.{final_status}", {"task_id": task_id, "attempt_id": attempt_id, "summary": final_summary}, task_id=task_id, attempt_id=attempt_id)
        except Exception as exc:
            finished_at = now_ms()
            message = redact_log(str(exc), limit=4000)
            state = load_run_state(workspace, attempt_id)
            state_started_at = int(state.get("started_at") or started_at)
            state.update({"status": "failed", "current_step_id": None, "finished_at": finished_at, "duration_seconds": round(max(0, finished_at - state_started_at) / 1000, 3), "summary": message})
            write_run_state(workspace, attempt_id, state)
            repo.update_attempt(attempt_id, status="failed", summary=message[:4000], finished_at=finished_at)
            repo.update_task(task_id, status="failed", latest_attempt_id=attempt_id, updated_at=finished_at)
            ctx.session_repo.update(session_id, status="failed", finished_at=finished_at, updated_at=finished_at)
            add_event(session_id, "dance_mimic_v1.attempt.failed", {"task_id": task_id, "attempt_id": attempt_id, "summary": message}, task_id=task_id, attempt_id=attempt_id)
        finally:
            with _RUN_LOCK:
                _ACTIVE_ATTEMPTS.discard(attempt_id)

    def run_dance_mimic_prepare_for_one_click(task: dict[str, Any], payload: DanceMimicRunPayload, run_id: str) -> dict[str, Any]:
        task_id = int(task["id"])
        session_id = int(task["session_id"])
        workspace = workspace_for(task)
        with _RUN_LOCK:
            active = sorted(_ACTIVE_ATTEMPTS)
            if active:
                raise RuntimeError(f"动作模拟已有运行中的尝试: {active}")
            attempt = repo.create_attempt(
                task_id=task_id,
                session_id=session_id,
                status="queued",
                run_model_provider=OPENROUTER_PROVIDER,
                run_model_id=OPENROUTER_SEEDANCE_MODEL,
                summary="一键成片自动准备故事版",
                created_at=now_ms(),
            )
            attempt_id = int(attempt["id"])
            _ACTIVE_ATTEMPTS.add(attempt_id)
            plan = compile_plan(payload)
            state = {
                "schema_version": "dance_mimic_v1_run_state_0.1",
                "task_id": task_id,
                "session_id": session_id,
                "attempt_id": attempt_id,
                "attempt_no": int(attempt["attempt_no"]),
                "target": DANCE_MIMIC_TARGET,
                "attempt_family": DANCE_MIMIC_ATTEMPT_FAMILY,
                "status": "queued",
                "provider": OPENROUTER_PROVIDER,
                "model": OPENROUTER_SEEDANCE_MODEL,
                "current_step_id": None,
                "steps": plan["steps"],
                "plan": {key: value for key, value in plan.items() if key != "steps"},
                "summary": "一键成片自动准备故事版",
                "created_at": now_ms(),
                "updated_at": now_ms(),
            }
            write_run_state(workspace, attempt_id, state)
            repo.update_task(task_id, status="queued", latest_attempt_id=attempt_id, run_model_provider=OPENROUTER_PROVIDER, run_model_id=OPENROUTER_SEEDANCE_MODEL, updated_at=now_ms())
            ctx.session_repo.update(session_id, status="queued", updated_at=now_ms())
        add_event(session_id, "dance_mimic_v1.one_click_movie.prepare_storyboard.created", {"task_id": task_id, "run_id": run_id, "attempt_id": attempt_id}, task_id=task_id, attempt_id=attempt_id)
        run_attempt_background(task_id=task_id, attempt_id=attempt_id, payload=payload)
        attempt = repo.get_attempt(attempt_id) or {}
        state = load_run_state(workspace, attempt_id)
        return {
            "attempt_id": attempt_id,
            "status": text_value(state.get("status") or attempt.get("status") or "failed"),
            "summary": text_value(state.get("summary") or attempt.get("summary")),
        }

    def start_run(task_id: int, payload: DanceMimicRunPayload) -> dict[str, Any]:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        task = normalize_legacy_session_context_inputs(task)
        workspace = workspace_for(task)
        validate_reference_video(text_value(task.get("reference_video_path")), workspace=workspace)
        meta = read_json(workspace / TASK_META_REL)
        validate_target_identity_image(target_identity_image_from_meta(meta), workspace=workspace)
        payload = run_payload_from_meta(meta, payload)
        if payload.target_video_seconds < payload.minimum_video_seconds:
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_split_config_invalid", "message": "target_video_seconds must be >= minimum_video_seconds."})
        session_id = int(task["session_id"])
        with _RUN_LOCK:
            with _ONE_CLICK_MOVIE_LOCK:
                movie_active = sorted(_ACTIVE_ONE_CLICK_MOVIE_RUNS)
                if movie_active:
                    raise HTTPException(status_code=409, detail={"code": "dance_mimic_one_click_movie_active_run_exists", "active_run_ids": movie_active})
            active = sorted(_ACTIVE_ATTEMPTS)
            if active:
                raise HTTPException(status_code=409, detail={"code": "dance_mimic_active_run_exists", "active_attempt_ids": active})
            attempt = repo.create_attempt(
                task_id=task_id,
                session_id=session_id,
                status="queued",
                run_model_provider=OPENROUTER_PROVIDER,
                run_model_id=OPENROUTER_SEEDANCE_MODEL,
                summary="",
                created_at=now_ms(),
            )
            attempt_id = int(attempt["id"])
            _ACTIVE_ATTEMPTS.add(attempt_id)
            plan = compile_plan(payload)
            state = {
                "schema_version": "dance_mimic_v1_run_state_0.1",
                "task_id": task_id,
                "session_id": session_id,
                "attempt_id": attempt_id,
                "attempt_no": int(attempt["attempt_no"]),
                "target": DANCE_MIMIC_TARGET,
                "attempt_family": DANCE_MIMIC_ATTEMPT_FAMILY,
                "status": "queued",
                "provider": OPENROUTER_PROVIDER,
                "model": OPENROUTER_SEEDANCE_MODEL,
                "current_step_id": None,
                "steps": plan["steps"],
                "plan": {key: value for key, value in plan.items() if key != "steps"},
                "created_at": now_ms(),
                "updated_at": now_ms(),
            }
            write_run_state(workspace, attempt_id, state)
            repo.update_task(task_id, status="queued", latest_attempt_id=attempt_id, run_model_provider=OPENROUTER_PROVIDER, run_model_id=OPENROUTER_SEEDANCE_MODEL, updated_at=now_ms())
            ctx.session_repo.update(session_id, status="queued", updated_at=now_ms())
        add_event(session_id, "dance_mimic_v1.attempt.created", {"task_id": task_id, "attempt_id": attempt_id, "attempt_no": int(attempt["attempt_no"])}, task_id=task_id, attempt_id=attempt_id)
        initial_status = run_status(task_id, attempt_id)
        thread = threading.Thread(target=run_attempt_background, kwargs={"task_id": task_id, "attempt_id": attempt_id, "payload": payload}, daemon=True)
        thread.start()
        return initial_status

    def run_status(task_id: int, attempt_id: int) -> dict[str, Any]:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        attempt = repo.get_attempt(attempt_id)
        if not attempt or int(attempt.get("task_id") or 0) != task_id:
            raise HTTPException(status_code=404, detail="Attempt not found")
        workspace = workspace_for(task)
        state = load_run_state(workspace, attempt_id)
        return {
            "ok": True,
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "attempt_id": attempt_id,
            "attempt_no": int(attempt.get("attempt_no") or state.get("attempt_no") or 0),
            "target": DANCE_MIMIC_TARGET,
            "attempt_family": DANCE_MIMIC_ATTEMPT_FAMILY,
            "status": text_value(state.get("status") or attempt.get("status") or "queued"),
            "current_step_id": state.get("current_step_id"),
            "provider": OPENROUTER_PROVIDER,
            "model": OPENROUTER_SEEDANCE_MODEL,
            "steps": state.get("steps") or [],
            "plan": state.get("plan") or {},
            "stale": stale_summary(workspace),
            "summary": text_value(state.get("summary") or attempt.get("summary")),
            "started_at": state.get("started_at") or attempt.get("started_at"),
            "finished_at": state.get("finished_at") or attempt.get("finished_at"),
            "duration_seconds": state.get("duration_seconds"),
            "result_manifest": state.get("result_manifest") or {},
            "workspace_dir": str(workspace),
            "updated_at": state.get("updated_at") or now_ms(),
        }

    def one_click_movie_step_payload(spec: dict[str, Any], status: str = "pending") -> dict[str, Any]:
        return {
            "id": str(spec["id"]),
            "name": str(spec["name"]),
            "status": status,
            "script": repo_relative(spec["script"]),
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "returncode": None,
            "tool_status": "",
            "message": "",
            "result_path": "",
            "stdout_tail": "",
            "stderr_tail": "",
            "argv": [],
        }

    def compile_one_click_movie_plan(payload: DanceMimicOneClickMoviePayload) -> dict[str, Any]:
        return {
            "target": "dance_mimic_v1_one_click_movie",
            "steps": [one_click_movie_step_payload(spec) for spec in ONE_CLICK_MOVIE_TOOL_SPECS],
            "options": {
                "target_type": "task",
                "max_video_seconds": safe_float(payload.max_video_seconds, 4.0),
                "min_video_seconds": safe_float(payload.min_video_seconds, 2.0),
                "split_tolerance_seconds": max(0.0, safe_float(payload.split_tolerance_seconds, 2.0)),
                "force": bool(payload.force),
                "resume": bool(payload.resume),
                "run_only_step_id": text_value(payload.run_only_step_id),
                "run_from_step_id": text_value(payload.run_from_step_id),
                "no_lipsync": True,
                "first_frame_policy": "previous_segment_tail_frame",
                "source_image_regeneration": "disabled",
                "subtitle_mode": text_value(payload.subtitle_mode) or "hyperframe",
                "watermark_mode": text_value(payload.watermark_mode) or "never",
            },
        }

    def one_click_movie_state_path(workspace: Path, run_id: str) -> Path:
        return workspace / "SessionReport" / "dance_mimic_v1" / "one_click_movie" / f"{run_id}.json"

    def write_one_click_movie_state(workspace: Path, state: dict[str, Any]) -> dict[str, Any]:
        state["updated_at"] = now_ms()
        run_id = text_value(state.get("run_id"))
        if run_id:
            write_json(one_click_movie_state_path(workspace, run_id), state)
        write_json(workspace / DANCE_MIMIC_ONE_CLICK_MOVIE_STATE_REL, state)
        return state

    def load_one_click_movie_state(workspace: Path, run_id: str = "") -> dict[str, Any]:
        if text_value(run_id):
            state = read_json(one_click_movie_state_path(workspace, text_value(run_id)))
            if state:
                return state
        return read_json(workspace / DANCE_MIMIC_ONE_CLICK_MOVIE_STATE_REL)

    def reconcile_stale_one_click_movie_state(workspace: Path, state: dict[str, Any], task_id: int, session_id: int) -> dict[str, Any]:
        run_id = text_value(state.get("run_id"))
        if not run_id or text_value(state.get("status")) not in {"queued", "running"}:
            return state
        with _ONE_CLICK_MOVIE_LOCK:
            if run_id in _ACTIVE_ONE_CLICK_MOVIE_RUNS:
                return state
        finished_at = now_ms()
        started_at = int(state.get("started_at") or finished_at)
        current_step_id = text_value(state.get("current_step_id"))
        message = "后台服务已重启，原一键成片运行进程已中断；请从失败步骤继续运行。"
        for step in state.get("steps") or []:
            if not isinstance(step, dict):
                continue
            is_current = text_value(step.get("id")) == current_step_id
            is_active_step = text_value(step.get("status")) in {"queued", "running"}
            if is_current or is_active_step:
                step_started_at = int(step.get("started_at") or started_at)
                step.update({
                    "status": "failed",
                    "finished_at": finished_at,
                    "duration_seconds": round(max(0, finished_at - step_started_at) / 1000, 3),
                    "returncode": None,
                    "tool_status": "abandoned",
                    "message": message,
                    "stderr_tail": message,
                })
        state.update({
            "status": "failed",
            "current_step_id": None,
            "finished_at": finished_at,
            "duration_seconds": round(max(0, finished_at - started_at) / 1000, 3),
            "summary": message,
            "updated_at": finished_at,
            "segments": one_click_movie_segments(workspace),
            "compose": one_click_movie_compose_summary(workspace),
        })
        write_one_click_movie_state(workspace, state)
        add_event(session_id, "dance_mimic_v1.one_click_movie.failed", {"task_id": task_id, "run_id": run_id, "summary": message}, task_id=task_id)
        return state

    def one_click_movie_command_for_step(spec: dict[str, Any], workspace: Path, run_id: str, payload: DanceMimicOneClickMoviePayload) -> list[str]:
        step_id = str(spec["id"])
        command = [python_executable(), str(spec["script"]), "--workspace", str(workspace)]
        if step_id == "05_01":
            command.extend([
                "--target-type",
                "task",
                "--max-video-seconds",
                str(safe_float(payload.max_video_seconds, 4.0)),
                "--min-video-seconds",
                str(safe_float(payload.min_video_seconds, 2.0)),
                "--split-tolerance-seconds",
                str(max(0.0, safe_float(payload.split_tolerance_seconds, 2.0))),
            ])
            if payload.force:
                command.append("--force")
            if payload.resume:
                command.append("--resume")
        elif step_id == "05_02":
            db_url = text_value(getattr(getattr(ctx, "config", None), "database_url", ""))
            if db_url:
                command.extend(["--database-url", db_url])
            command.extend(["--execution-job-id", run_id, "--no-execute-lipsync", "--execute-audio-video-sync"])
            if payload.force:
                command.append("--force")
            if text_value(payload.video_provider):
                command.extend(["--video-provider", text_value(payload.video_provider)])
            if text_value(payload.video_model):
                command.extend(["--video-model", text_value(payload.video_model)])
        elif step_id == "06_01":
            command.extend([
                "--target-type",
                "task",
                "--subtitle-mode",
                text_value(payload.subtitle_mode) or "hyperframe",
                "--watermark-mode",
                text_value(payload.watermark_mode) or "never",
            ])
            if payload.force:
                command.append("--force")
            if payload.resume:
                command.append("--resume")
        command.append("--print-json")
        return command

    def one_click_movie_selected_specs(payload: DanceMimicOneClickMoviePayload) -> list[dict[str, Any]]:
        run_only_step_id = text_value(payload.run_only_step_id)
        run_from_step_id = text_value(payload.run_from_step_id)
        if not run_only_step_id:
            if run_from_step_id:
                for index, spec in enumerate(ONE_CLICK_MOVIE_TOOL_SPECS):
                    if str(spec.get("id")) == run_from_step_id:
                        return ONE_CLICK_MOVIE_TOOL_SPECS[index:]
                return []
            return ONE_CLICK_MOVIE_TOOL_SPECS
        return [spec for spec in ONE_CLICK_MOVIE_TOOL_SPECS if str(spec.get("id")) == run_only_step_id]

    def mark_one_click_movie_unselected_steps(state: dict[str, Any], selected_step_ids: set[str]) -> None:
        if not selected_step_ids:
            return
        for step in state.get("steps") or []:
            step_id = text_value(step.get("id"))
            if step_id in selected_step_ids:
                continue
            if step_id == "06_01" and "05_02" in selected_step_ids:
                step.update({"status": "pending", "message": "本次只续跑 05-02，合并成片保持等待。"})
            else:
                step.update({"status": "skipped", "message": "本次未重新执行。"})

    def flatten_video_plan_segments(plan: dict[str, Any]) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for shot in sequence_items(plan.get("shots")):
            if not isinstance(shot, dict):
                continue
            for scene in sequence_items(shot.get("scenes")):
                if not isinstance(scene, dict):
                    continue
                for segment in sequence_items(scene.get("segments")):
                    if isinstance(segment, dict):
                        segments.append(segment)
        return segments

    def validate_one_click_movie_plan_contract(workspace: Path) -> None:
        plan = read_json(workspace / "SessionOutput/storyboard/video_generation_plan.json")
        segments = flatten_video_plan_segments(plan)
        if not segments:
            raise RuntimeError("video_generation_plan.json 没有可执行的逐句视频段。")
        for index, segment in enumerate(segments):
            asset_key = text_value(segment.get("asset_key") or segment.get("segment_id") or f"segment_{index + 1}")
            tasks = segment.get("tasks") if isinstance(segment.get("tasks"), dict) else {}
            if bool(tasks.get("need_lipsync")):
                raise RuntimeError(f"{asset_key} 计划要求 need_lipsync=true，已阻断一键成片。")
            if text_value(tasks.get("sync_mode")) not in {"", "audio_replace_retime"}:
                raise RuntimeError(f"{asset_key} 不是 audio_replace_retime 音频合成模式，已阻断一键成片。")
            if index > 0:
                first_frame = segment.get("first_frame") if isinstance(segment.get("first_frame"), dict) else {}
                if text_value(first_frame.get("source_type")) != "previous_segment_tail_frame":
                    raise RuntimeError(f"{asset_key} 首帧不是上一句尾帧，已阻断一键成片。")
                materialize = first_frame.get("materialize_first_frame") if isinstance(first_frame.get("materialize_first_frame"), dict) else {}
                copy_from = text_value(materialize.get("copy_from_path") or first_frame.get("source_path"))
                copy_to = text_value(materialize.get("copy_to_path") or first_frame.get("planned_generated_image_path"))
                if not copy_from or not copy_to:
                    raise RuntimeError(f"{asset_key} 缺少尾帧复制到首帧的路径，已阻断一键成片。")

    def one_click_file_status(workspace: Path, rel_path: str) -> dict[str, Any]:
        value = text_value(rel_path)
        if not value:
            return {"path": "", "exists": False, "size": 0}
        path = path_in_workspace(workspace, value)
        exists = path.is_file()
        return {"path": value, "exists": exists, "size": int(path.stat().st_size) if exists else 0}

    def one_click_image_source_type(first_frame: dict[str, Any], materialize: dict[str, Any]) -> str:
        return text_value(materialize.get("source_type") or first_frame.get("source_type"))

    def one_click_image_input_kind(first_frame: dict[str, Any], materialize: dict[str, Any], first_frame_status: dict[str, Any]) -> str:
        source_type = one_click_image_source_type(first_frame, materialize)
        if source_type in {"previous_segment_tail_frame", "previous_scene_tail_frame", "tail_frame_materialized"}:
            if bool(first_frame_status.get("exists")):
                return "tail_frame_materialized"
            if bool(materialize.get("required")) or text_value(materialize.get("copy_from_path") or first_frame.get("source_path")):
                return "tail_frame_pending_copy"
        return "new_image"

    def one_click_image_step_label(first_frame: dict[str, Any], materialize: dict[str, Any], first_frame_status: dict[str, Any]) -> str:
        image_input_kind = one_click_image_input_kind(first_frame, materialize, first_frame_status)
        if image_input_kind == "tail_frame_materialized":
            return "尾帧作为新图"
        if image_input_kind == "tail_frame_pending_copy":
            return "尾帧"
        source_type = one_click_image_source_type(first_frame, materialize)
        if source_type in {"previous_segment_tail_frame", "previous_scene_tail_frame"}:
            return "尾帧作为新图" if bool(first_frame_status.get("exists")) else "尾帧"
        return "新图"

    def one_click_movie_segments(workspace: Path) -> list[dict[str, Any]]:
        plan = read_json(workspace / "SessionOutput/storyboard/video_generation_plan.json")
        execution_state = read_json(workspace / "SessionOutput/storyboard/video_plan_execution_state.json")
        execution_segments = execution_state.get("segments") if isinstance(execution_state.get("segments"), dict) else {}
        items: list[dict[str, Any]] = []
        for index, segment in enumerate(flatten_video_plan_segments(plan), start=1):
            segment_id = text_value(segment.get("segment_id"))
            asset_key = text_value(segment.get("asset_key") or segment_id or f"segment_{index:03d}")
            segment_state = execution_segments.get(segment_id) if isinstance(execution_segments.get(segment_id), dict) else {}
            steps = segment_state.get("steps") if isinstance(segment_state.get("steps"), dict) else {}
            first_frame = segment.get("first_frame") if isinstance(segment.get("first_frame"), dict) else {}
            materialize = first_frame.get("materialize_first_frame") if isinstance(first_frame.get("materialize_first_frame"), dict) else {}
            planned_outputs = segment.get("planned_outputs") if isinstance(segment.get("planned_outputs"), dict) else {}
            tail_frame = segment.get("tail_frame") if isinstance(segment.get("tail_frame"), dict) else {}
            first_frame_path = text_value(materialize.get("copy_to_path") or first_frame.get("planned_generated_image_path") or segment.get("first_frame_image_path"))
            tail_frame_path = text_value(tail_frame.get("planned_path"))
            final_video_path = text_value(planned_outputs.get("final_video_path") or planned_outputs.get("video_path"))
            raw_video_path = text_value(planned_outputs.get("raw_video_path"))
            audio_path = text_value(planned_outputs.get("segment_audio_path") or segment.get("segment_audio_path"))
            segment_status = text_value(segment_state.get("status"))
            if not segment_status:
                segment_status = "completed" if final_video_path and (workspace / final_video_path).is_file() else "pending"
            first_frame_status = one_click_file_status(workspace, first_frame_path)
            image_input_kind = one_click_image_input_kind(first_frame, materialize, first_frame_status)
            items.append({
                "index": index,
                "segment_id": segment_id,
                "asset_key": asset_key,
                "dialogue_ids": segment.get("dialogue_ids") or segment.get("dialogue_asset_keys") or [],
                "status": segment_status,
                "first_frame_policy": text_value(first_frame.get("source_type")),
                "first_frame_source_type": one_click_image_source_type(first_frame, materialize),
                "image_input_kind": image_input_kind,
                "image_step_label": one_click_image_step_label(first_frame, materialize, first_frame_status),
                "first_frame": {
                    "source_type": text_value(first_frame.get("source_type")),
                    "materialize_source_type": text_value(materialize.get("source_type")),
                    "materialize_required": bool(materialize.get("required")),
                    "source_path": text_value(first_frame.get("source_path")),
                    "copy_from_path": text_value(materialize.get("copy_from_path") or first_frame.get("source_path")),
                    "copy_to_path": first_frame_path,
                    "image_input_kind": image_input_kind,
                    "image_step_label": one_click_image_step_label(first_frame, materialize, first_frame_status),
                },
                "tail_dependency": {
                    "depends_on_segment_id": text_value((segment.get("dependencies") or {}).get("depends_on_segment_id") if isinstance(segment.get("dependencies"), dict) else ""),
                    "copy_from_path": text_value(materialize.get("copy_from_path") or first_frame.get("source_path")),
                    "copy_to_path": first_frame_path,
                },
                "steps": steps,
                "files": {
                    "audio": one_click_file_status(workspace, audio_path),
                    "first_frame": first_frame_status,
                    "raw_video": one_click_file_status(workspace, raw_video_path),
                    "final_video": one_click_file_status(workspace, final_video_path),
                    "tail_frame": one_click_file_status(workspace, tail_frame_path),
                },
                "lipsync": {
                    "need_lipsync": bool((segment.get("tasks") if isinstance(segment.get("tasks"), dict) else {}).get("need_lipsync")),
                    "sync_mode": text_value((segment.get("tasks") if isinstance(segment.get("tasks"), dict) else {}).get("sync_mode")),
                    "reason": text_value((segment.get("tasks") if isinstance(segment.get("tasks"), dict) else {}).get("lipsync_reason")),
                },
                "error": text_value(segment_state.get("error")),
            })
        return items

    def one_click_movie_compose_summary(workspace: Path) -> dict[str, Any]:
        result = read_json(workspace / "SessionOutput/storyboard/video_plan_compose_result.json")
        shot_plan = result.get("shot_plan") if isinstance(result.get("shot_plan"), dict) else {}
        shot_plan_outputs = shot_plan.get("outputs") if isinstance(shot_plan.get("outputs"), dict) else {}
        shot_plan_compose = shot_plan.get("compose") if isinstance(shot_plan.get("compose"), dict) else {}
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        output_video = text_value(
            shot_plan.get("output_video")
            or summary.get("output_video")
            or shot_plan_outputs.get("shot_plan_subtitled_video_path")
            or shot_plan_outputs.get("shot_plan_video_path")
            or shot_plan_compose.get("output_path")
        )
        return {
            "exists": bool(result),
            "status": text_value(result.get("status")),
            "output_video": output_video,
            "summary": summary,
        }

    def one_click_movie_status(task_id: int, run_id: str = "") -> dict[str, Any]:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        workspace = workspace_for(task)
        state = load_one_click_movie_state(workspace, run_id)
        if not state:
            state = {
                "schema_version": "dance_mimic_v1_one_click_movie_state_0.1",
                "task_id": task_id,
                "session_id": int(task["session_id"]),
                "run_id": "",
                "status": "idle",
                "steps": compile_one_click_movie_plan(DanceMimicOneClickMoviePayload())["steps"],
                "summary": "",
            }
        else:
            state = reconcile_stale_one_click_movie_state(workspace, state, task_id, int(task["session_id"]))
        return {
            "ok": True,
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "run_id": text_value(state.get("run_id")),
            "target": "dance_mimic_v1_one_click_movie",
            "status": text_value(state.get("status") or "idle"),
            "current_step_id": state.get("current_step_id"),
            "steps": state.get("steps") or [],
            "plan": state.get("plan") or {},
            "segments": one_click_movie_segments(workspace),
            "compose": one_click_movie_compose_summary(workspace),
            "summary": text_value(state.get("summary")),
            "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"),
            "duration_seconds": state.get("duration_seconds"),
            "workspace_dir": str(workspace),
            "updated_at": state.get("updated_at") or now_ms(),
        }

    def run_one_click_movie_step(
        *,
        task: dict[str, Any],
        run_id: str,
        payload: DanceMimicOneClickMoviePayload,
        spec: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[str, str]:
        task_id = int(task["id"])
        session_id = int(task["session_id"])
        workspace = workspace_for(task)
        step_id = str(spec["id"])
        step = next(item for item in state["steps"] if item["id"] == step_id)
        started_at = now_ms()
        command = one_click_movie_command_for_step(spec, workspace, run_id, payload)
        step.update({"status": "running", "started_at": started_at, "finished_at": None, "duration_seconds": None, "argv": command})
        state["status"] = "running"
        state["current_step_id"] = step_id
        write_one_click_movie_state(workspace, state)
        add_event(session_id, "dance_mimic_v1.one_click_movie.step.started", {"task_id": task_id, "run_id": run_id, "step_id": step_id}, task_id=task_id, step_id=step_id)
        completed = subprocess.run(
            command,
            cwd=str(OPENCREW_REPO_ROOT),
            env=dance_mimic_tool_env(ctx, task_id, session_id, 0, step_id),
            capture_output=True,
            text=True,
            check=False,
            timeout=int(spec.get("timeout") or 3600),
        )
        parsed = parse_stdout_json(completed.stdout)
        tool_status = text_value(parsed.get("status"))
        result_rel = text_value((parsed.get("outputs") or {}).get("result_path") if isinstance(parsed.get("outputs"), dict) else "")
        if not result_rel:
            result_rel = text_value(parsed.get("result_path"))
        error_payload = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
        message = text_value(parsed.get("message") or error_payload.get("message"))
        if not message:
            reasons = parsed.get("blocked_reasons") if isinstance(parsed.get("blocked_reasons"), list) else []
            message = "; ".join(text_value(item.get("message") or item.get("code")) for item in reasons if isinstance(item, dict))
        if completed.returncode == 0 and tool_status not in {"failed", "blocked"}:
            step_status = "completed"
        elif completed.returncode == 2 or tool_status == "blocked":
            step_status = "blocked"
        else:
            step_status = "failed"
        finished_at = now_ms()
        step.update({
            "status": step_status,
            "finished_at": finished_at,
            "duration_seconds": round(max(0, finished_at - started_at) / 1000, 3),
            "returncode": completed.returncode,
            "tool_status": tool_status,
            "message": message,
            "result_path": result_rel,
            "stdout_tail": redact_log(completed.stdout),
            "stderr_tail": redact_log(completed.stderr),
        })
        write_one_click_movie_state(workspace, state)
        add_event(session_id, f"dance_mimic_v1.one_click_movie.step.{step_status}", {"task_id": task_id, "run_id": run_id, "step_id": step_id, "message": message}, task_id=task_id, step_id=step_id)
        if step_status == "completed" and step_id == "05_01":
            validate_one_click_movie_plan_contract(workspace)
        return step_status, message or f"{step_id} {step_status}"

    def run_one_click_movie_background(task_id: int, run_id: str, payload: DanceMimicOneClickMoviePayload) -> None:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        workspace = workspace_for(task)
        session_id = int(task["session_id"])
        started_at = now_ms()
        final_status = "completed"
        run_only_step_id = text_value(payload.run_only_step_id)
        run_from_step_id = text_value(payload.run_from_step_id)
        final_summary = "05-02 已按当前 Video Plan 补跑完成" if run_only_step_id == "05_02" else "一键成片后续步骤完成" if run_from_step_id else "一键成片完成"
        try:
            state = load_one_click_movie_state(workspace, run_id)
            state.update({"status": "running", "started_at": started_at, "current_step_id": None})
            selected_specs = one_click_movie_selected_specs(payload)
            selected_step_ids = {str(spec["id"]) for spec in selected_specs}
            mark_one_click_movie_unselected_steps(state, selected_step_ids)
            write_one_click_movie_state(workspace, state)
            add_event(session_id, "dance_mimic_v1.one_click_movie.started", {"task_id": task_id, "run_id": run_id}, task_id=task_id)
            if run_only_step_id == "05_02" or run_from_step_id == "05_02":
                validate_one_click_movie_plan_contract(workspace)
            if run_only_step_id and not selected_specs:
                raise RuntimeError(f"不支持的一键成片步骤: {run_only_step_id}")
            if run_from_step_id and not selected_specs:
                raise RuntimeError(f"不支持的一键成片起始步骤: {run_from_step_id}")
            prepare_storyboard = bool((state.get("plan") or {}).get("options", {}).get("prepare_storyboard"))
            if prepare_storyboard:
                state.update({"summary": "正在准备故事版，完成后将继续一键成片。"})
                write_one_click_movie_state(workspace, state)
                meta = read_json(workspace / TASK_META_REL)
                prepare_payload = run_payload_from_meta(meta)
                prepare_result = run_dance_mimic_prepare_for_one_click(task=task, payload=prepare_payload, run_id=run_id)
                if prepare_result["status"] != "completed":
                    final_status = "blocked" if prepare_result["status"] == "blocked" else "failed"
                    final_summary = f"准备故事版未完成: {prepare_result['summary'] or prepare_result['status']}"
                    selected_specs = []
                else:
                    state = load_one_click_movie_state(workspace, run_id)
                    state.update({"summary": "故事版已准备完成，继续一键成片。"})
                    state["segments"] = one_click_movie_segments(workspace)
                    write_one_click_movie_state(workspace, state)
            for spec in selected_specs:
                step_status, message = run_one_click_movie_step(task=task, run_id=run_id, payload=payload, spec=spec, state=state)
                state["segments"] = one_click_movie_segments(workspace)
                write_one_click_movie_state(workspace, state)
                if step_status == "blocked":
                    final_status = "blocked"
                    final_summary = message
                    break
                if step_status != "completed":
                    final_status = "failed"
                    final_summary = message
                    break
            finished_at = now_ms()
            duration_seconds = round(max(0, finished_at - started_at) / 1000, 3)
            state.update({
                "status": final_status,
                "current_step_id": None,
                "finished_at": finished_at,
                "duration_seconds": duration_seconds,
                "summary": final_summary,
                "segments": one_click_movie_segments(workspace),
                "compose": one_click_movie_compose_summary(workspace),
            })
            write_one_click_movie_state(workspace, state)
            add_event(session_id, f"dance_mimic_v1.one_click_movie.{final_status}", {"task_id": task_id, "run_id": run_id, "summary": final_summary}, task_id=task_id)
        except Exception as exc:
            finished_at = now_ms()
            message = redact_log(str(exc), limit=4000)
            state = load_one_click_movie_state(workspace, run_id)
            state_started_at = int(state.get("started_at") or started_at)
            state.update({
                "status": "blocked",
                "current_step_id": None,
                "finished_at": finished_at,
                "duration_seconds": round(max(0, finished_at - state_started_at) / 1000, 3),
                "summary": message,
                "segments": one_click_movie_segments(workspace),
                "compose": one_click_movie_compose_summary(workspace),
            })
            write_one_click_movie_state(workspace, state)
            add_event(session_id, "dance_mimic_v1.one_click_movie.blocked", {"task_id": task_id, "run_id": run_id, "summary": message}, task_id=task_id)
        finally:
            with _ONE_CLICK_MOVIE_LOCK:
                _ACTIVE_ONE_CLICK_MOVIE_RUNS.discard(run_id)

    def start_one_click_movie(task_id: int, payload: DanceMimicOneClickMoviePayload) -> dict[str, Any]:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        task = normalize_legacy_session_context_inputs(task)
        workspace = workspace_for(task)
        artifacts = artifact_summary(workspace)
        prepare_storyboard = not bool(artifacts.get("storyboard_ready"))
        if prepare_storyboard:
            meta = read_json(workspace / TASK_META_REL)
            validate_reference_video(text_value(task.get("reference_video_path")), workspace=workspace)
            validate_target_identity_image(target_identity_image_from_meta(meta), workspace=workspace)
            prepare_payload = run_payload_from_meta(meta)
            if prepare_payload.target_video_seconds < prepare_payload.minimum_video_seconds:
                raise HTTPException(status_code=400, detail={"code": "dance_mimic_split_config_invalid", "message": "target_video_seconds must be >= minimum_video_seconds."})
        if safe_float(payload.max_video_seconds, 4.0) < safe_float(payload.min_video_seconds, 2.0):
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_one_click_video_seconds_invalid", "message": "max_video_seconds must be >= min_video_seconds."})
        session_id = int(task["session_id"])
        with _RUN_LOCK:
            active_attempts = sorted(_ACTIVE_ATTEMPTS)
            if active_attempts:
                raise HTTPException(status_code=409, detail={"code": "dance_mimic_active_run_exists", "active_attempt_ids": active_attempts})
            with _ONE_CLICK_MOVIE_LOCK:
                active = sorted(_ACTIVE_ONE_CLICK_MOVIE_RUNS)
                if active:
                    raise HTTPException(status_code=409, detail={"code": "dance_mimic_one_click_movie_active_run_exists", "active_run_ids": active})
                run_id = str(now_ms())
                _ACTIVE_ONE_CLICK_MOVIE_RUNS.add(run_id)
                plan = compile_one_click_movie_plan(payload)
                plan["options"]["prepare_storyboard"] = prepare_storyboard
                state = {
                    "schema_version": "dance_mimic_v1_one_click_movie_state_0.1",
                    "task_id": task_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "target": "dance_mimic_v1_one_click_movie",
                    "status": "queued",
                    "current_step_id": None,
                    "steps": plan["steps"],
                    "plan": {key: value for key, value in plan.items() if key != "steps"},
                    "summary": "",
                    "created_at": now_ms(),
                    "updated_at": now_ms(),
                }
                write_one_click_movie_state(workspace, state)
        add_event(session_id, "dance_mimic_v1.one_click_movie.created", {"task_id": task_id, "run_id": run_id}, task_id=task_id)
        initial_status = one_click_movie_status(task_id, run_id)
        thread = threading.Thread(target=run_one_click_movie_background, kwargs={"task_id": task_id, "run_id": run_id, "payload": payload}, daemon=True)
        thread.start()
        return initial_status

    def artifact_entry(workspace: Path, rel_path: str) -> dict[str, Any]:
        path = workspace / rel_path
        exists = path.is_file()
        return {
            "path": rel_path,
            "exists": exists,
            "size": int(path.stat().st_size) if exists else 0,
        }

    def target_identity_artifact_entry(workspace: Path) -> dict[str, Any]:
        for suffix in TARGET_IDENTITY_IMAGE_EXT_ORDER:
            rel_path = f"{TARGET_IDENTITY_IMAGE_REL_PREFIX}{suffix}"
            entry = artifact_entry(workspace, rel_path)
            if entry["exists"]:
                return entry
        return artifact_entry(workspace, f"{TARGET_IDENTITY_IMAGE_REL_PREFIX}.png")

    def artifact_summary(workspace: Path) -> dict[str, Any]:
        files = {
            "variables_json": artifact_entry(workspace, "SessionContext/Variables.json"),
            "source_reference_video": artifact_entry(workspace, "SessionContext/Video_Reference_Source.mp4"),
            "target_identity_image": target_identity_artifact_entry(workspace),
            "reference_media_manifest": artifact_entry(workspace, "SessionOutput/reference/reference_media_manifest.json"),
            "reference_segments_manifest": artifact_entry(workspace, "SessionOutput/reference/segments/reference_segments_manifest.json"),
            "srt_storyboard": artifact_entry(workspace, STORYBOARD_REL),
            "storyboard_seed": artifact_entry(workspace, STORYBOARD_SEED_REL),
            "stale_manifest": artifact_entry(workspace, DANCE_MIMIC_STALE_MANIFEST_REL),
        }
        return {
            "files": files,
            "reference_ready": bool(files["reference_segments_manifest"]["exists"]),
            "storyboard_ready": bool(files["srt_storyboard"]["exists"] and files["storyboard_seed"]["exists"]),
        }

    def sequence_items(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def number_value(value: Any, fallback: float = 0.0) -> float:
        try:
            parsed = float(value)
            return parsed if parsed == parsed else fallback
        except Exception:
            return fallback

    def seed_segments_by_key(seed: dict[str, Any]) -> dict[str, dict[str, Any]]:
        lookup: dict[str, dict[str, Any]] = {}
        for segment in sequence_items(seed.get("segments")):
            if not isinstance(segment, dict):
                continue
            for key in (text_value(segment.get("srt_id")), text_value(segment.get("dialogue_asset_key")), text_value(segment.get("segment_id"))):
                if key:
                    lookup[key] = segment
        return lookup

    def media_preview_entry(workspace: Path, rel_path: str) -> dict[str, Any]:
        value = text_value(rel_path)
        if not value:
            return {"path": "", "exists": False, "preview_url": "", "size": 0}
        path = path_in_workspace(workspace, value)
        exists = path.is_file()
        return {
            "path": value,
            "exists": exists,
            "preview_url": reference_video_preview_url(path) if exists else "",
            "size": int(path.stat().st_size) if exists else 0,
        }

    def image_preview_entry(workspace: Path, rel_path: str) -> dict[str, Any]:
        value = text_value(rel_path)
        if not value:
            return {"path": "", "exists": False, "preview_url": "", "size": 0}
        path = path_in_workspace(workspace, value)
        exists = path.is_file()
        return {
            "path": value,
            "exists": exists,
            "preview_url": target_image_preview_url(path) if exists else "",
            "size": int(path.stat().st_size) if exists else 0,
        }

    def storyboard_breakdown(workspace: Path) -> dict[str, Any]:
        storyboard_path = workspace / STORYBOARD_REL
        seed_path = workspace / STORYBOARD_SEED_REL
        storyboard = read_json(storyboard_path)
        seed = read_json(seed_path)
        seed_lookup = seed_segments_by_key(seed)
        items: list[dict[str, Any]] = []
        for shot in sequence_items(storyboard.get("shots")):
            if not isinstance(shot, dict):
                continue
            shot_id = text_value(shot.get("shot_id") or shot.get("id"))
            for scene in sequence_items(shot.get("scenes")):
                if not isinstance(scene, dict):
                    continue
                scene_id = text_value(scene.get("scene_id") or scene.get("id"))
                for dialogue in sequence_items(scene.get("dialogue_items")):
                    if not isinstance(dialogue, dict):
                        continue
                    srt_id = text_value(dialogue.get("srt_id") or dialogue.get("dialogue_id"))
                    asset_key = text_value(dialogue.get("dialogue_asset_key"))
                    dance_mimic = dialogue.get("dance_mimic") if isinstance(dialogue.get("dance_mimic"), dict) else {}
                    seed_segment = seed_lookup.get(srt_id) or seed_lookup.get(asset_key) or seed_lookup.get(text_value(dance_mimic.get("source_segment_id"))) or {}
                    reference_video_path = text_value(dance_mimic.get("reference_video_path") or seed_segment.get("reference_video_path") or seed_segment.get("source_face_masked_reference_video_path"))
                    target_image_path = text_value(dialogue.get("image_path") or dance_mimic.get("target_identity_image_path") or seed_segment.get("target_identity_image_path"))
                    working_assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
                    working_audio = working_assets.get("audio") if isinstance(working_assets.get("audio"), dict) else {}
                    audio_path = text_value(working_audio.get("path") or dance_mimic.get("segment_audio_source_path") or seed_segment.get("audio_path"))
                    items.append({
                        "index": len(items) + 1,
                        "srt_id": srt_id or f"srt_{len(items) + 1:04d}",
                        "dialogue_id": text_value(dialogue.get("dialogue_id")),
                        "dialogue_asset_key": asset_key,
                        "shot_id": shot_id,
                        "scene_id": scene_id,
                        "text": text_value(dialogue.get("dialogue")),
                        "start": number_value(dialogue.get("start"), number_value(seed_segment.get("start_seconds"))),
                        "end": number_value(dialogue.get("end"), number_value(seed_segment.get("end_seconds"))),
                        "duration": number_value(dialogue.get("duration"), number_value(seed_segment.get("duration_seconds"))),
                        "source_segment_id": text_value(dance_mimic.get("source_segment_id") or seed_segment.get("segment_id")),
                        "audio": media_preview_entry(workspace, audio_path),
                        "reference_video": media_preview_entry(workspace, reference_video_path),
                        "target_image": image_preview_entry(workspace, target_image_path),
                    })
        return {
            "exists": storyboard_path.is_file(),
            "ready": bool(storyboard_path.is_file() and items),
            "path": STORYBOARD_REL,
            "seed_path": STORYBOARD_SEED_REL,
            "item_count": len(items),
            "items": items,
        }

    def latest_attempt(task: dict[str, Any]) -> dict[str, Any] | None:
        attempt_id = int(task.get("latest_attempt_id") or 0)
        if attempt_id:
            attempt = repo.get_attempt(attempt_id)
            if attempt and int(attempt.get("task_id") or 0) == int(task["id"]):
                return attempt
        attempts = repo.list_attempts(int(task["id"]))
        return attempts[0] if attempts else None

    def task_detail(task_id: int) -> dict[str, Any]:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        task = normalize_legacy_session_context_inputs(task)
        workspace = workspace_for(task)
        meta = read_json(workspace / TASK_META_REL)
        attempt = latest_attempt(task)
        latest_run = run_status(task_id, int(attempt["id"])) if attempt else None
        return {
            "ok": True,
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "workflow_mode": WORKFLOW_DANCE_MIMIC_V1,
            "workflow_id": WORKFLOW_DANCE_MIMIC_V1,
            "target": DANCE_MIMIC_TARGET,
            "task": serialize_item(task_id),
            "meta": meta,
            "reference_video_path": text_value(task.get("reference_video_path") or meta.get("reference_video_path")),
            "target_identity_image_path": target_identity_image_from_meta(meta),
            "reference_privacy_mode": reference_privacy_mode_from_meta(meta),
            "apply_privacy_grid_to_reference_video": privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_reference_video"),
            "apply_privacy_grid_to_target_identity_image": privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_target_identity_image"),
            "effective_grid_scope": effective_grid_scope(privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_reference_video"), privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_target_identity_image"), reference_privacy_mode_from_meta(meta)),
            "workspace_dir": str(workspace),
            "latest_run": latest_run,
            "latest_movie_run": one_click_movie_status(task_id),
            "artifacts": artifact_summary(workspace),
            "stale": stale_summary(workspace),
            "privacy_grid_preview": privacy_grid_preview_summary(task_id, workspace, meta, latest_run),
            "storyboard": storyboard_breakdown(workspace),
            "run_plan": compile_plan(run_payload_from_meta(meta)),
            "movie_plan": compile_one_click_movie_plan(DanceMimicOneClickMoviePayload()),
        }

    def serialize_item(task_id: int) -> dict[str, Any]:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        task = normalize_legacy_session_context_inputs(task)
        workspace = workspace_for(task)
        meta = read_json(workspace / TASK_META_REL)
        return {
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "workflow_mode": WORKFLOW_DANCE_MIMIC_V1,
            "workflow_id": WORKFLOW_DANCE_MIMIC_V1,
            "create_mode": "dance_mimic",
            "input_mode": "reference_video",
            "title": text_value(meta.get("title") or task.get("title") or f"DanceMimic Task #{task_id}"),
            "status": text_value(task.get("status") or "draft"),
            "reference_video": text_value(task.get("reference_video_path")),
            "target_identity_image": target_identity_image_from_meta(meta),
            "reference_privacy_mode": reference_privacy_mode_from_meta(meta),
            "apply_privacy_grid_to_reference_video": privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_reference_video"),
            "apply_privacy_grid_to_target_identity_image": privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_target_identity_image"),
            "effective_grid_scope": effective_grid_scope(privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_reference_video"), privacy_grid_flag_from_meta(meta, "apply_privacy_grid_to_target_identity_image"), reference_privacy_mode_from_meta(meta)),
            "analysis_url": f"#/dance-mimic/tasks/{task_id}",
            "storyboard_url": f"#/koubo-storyboard/tasks/{task_id}",
            "workspace_dir": str(workspace),
            "updated_at": int(task.get("updated_at") or 0),
        }

    @router.get("/api/dance-mimic-v1/target-images")
    async def list_target_identity_images(limit: int = Query(80, ge=1, le=200), ai_person_only: bool = True) -> dict[str, Any]:
        items = collect_target_image_candidates(limit, ai_person_only=ai_person_only)
        return {
            "ok": True,
            "items": items,
            "count": len(items),
            "ai_person_only": bool(ai_person_only),
            "upload_supported_exts": sorted(SUPPORTED_TARGET_IMAGE_EXTS),
        }

    @router.get("/api/dance-mimic-v1/target-images/preview")
    async def preview_target_identity_image(path: str = Query(..., min_length=1)) -> FileResponse:
        target = ensure_previewable_target_image(path)
        return FileResponse(target, media_type=target_image_media_type(target), filename=target.name)

    @router.post("/api/dance-mimic-v1/target-images/upload")
    async def upload_target_identity_image(file: UploadFile = File(...)) -> dict[str, Any]:
        filename = safe_upload_filename(file.filename or "target_identity.png")
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_TARGET_IMAGE_EXTS:
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_target_identity_image_unsupported", "message": f"Target identity image must be one of: {', '.join(sorted(SUPPORTED_TARGET_IMAGE_EXTS))}"})
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_target_identity_image_empty", "message": "Uploaded target identity image is empty."})
        target = unique_upload_path(target_image_upload_root() / f"{now_ms()}_{filename}", "dance_mimic_target_image_name_conflict")
        target.write_bytes(content)
        item = target_image_candidate(target, "uploaded", [])
        return {
            "ok": True,
            "item": item,
            "target_identity_image_path": str(target),
            "items": [item],
        }

    @router.get("/api/dance-mimic-v1/reference-videos")
    async def list_reference_videos(limit: int = Query(80, ge=1, le=200)) -> dict[str, Any]:
        items = collect_reference_video_candidates(limit)
        return {
            "ok": True,
            "items": items,
            "count": len(items),
            "upload_supported_exts": sorted(SUPPORTED_REFERENCE_VIDEO_EXTS),
        }

    @router.get("/api/dance-mimic-v1/reference-videos/preview")
    async def preview_reference_video(path: str = Query(..., min_length=1)) -> FileResponse:
        source = ensure_previewable_reference_video(path)
        return FileResponse(source, media_type=reference_video_media_type(source), filename=source.name)

    @router.post("/api/dance-mimic-v1/reference-videos/upload")
    async def upload_reference_video(file: UploadFile = File(...)) -> dict[str, Any]:
        original_suffix = Path(text_value(file.filename or "")).suffix.lower()
        if original_suffix and original_suffix not in SUPPORTED_REFERENCE_VIDEO_EXTS:
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_reference_video_unsupported", "message": f"Reference video must be one of: {', '.join(sorted(SUPPORTED_REFERENCE_VIDEO_EXTS))}"})
        filename = safe_upload_filename(file.filename or "reference.mp4", "reference.mp4", SUPPORTED_REFERENCE_VIDEO_EXTS)
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_REFERENCE_VIDEO_EXTS:
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_reference_video_unsupported", "message": f"Reference video must be one of: {', '.join(sorted(SUPPORTED_REFERENCE_VIDEO_EXTS))}"})
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_reference_video_empty", "message": "Uploaded reference video is empty."})
        target = unique_upload_path(reference_video_upload_root() / f"{now_ms()}_{filename}", "dance_mimic_reference_video_name_conflict")
        target.write_bytes(content)
        item = reference_video_candidate(target, "uploaded", [])
        return {
            "ok": True,
            "item": item,
            "reference_video_path": str(target),
            "items": [item],
        }

    def dance_config_from_payload(payload: DanceMimicTaskCreatePayload, source: Path | None, target_identity: Path | None, workspace: Path | None = None) -> dict[str, Any]:
        return {
            "reference_video_path": path_for_task_config(workspace, source),
            "target_identity_image_path": path_for_task_config(workspace, target_identity),
            "reference_privacy_mode": text_value(payload.reference_privacy_mode) or DEFAULT_REFERENCE_PRIVACY_MODE,
            "apply_privacy_grid_to_reference_video": bool(payload.apply_privacy_grid_to_reference_video),
            "apply_privacy_grid_to_target_identity_image": bool(payload.apply_privacy_grid_to_target_identity_image),
            "effective_grid_scope": effective_grid_scope(bool(payload.apply_privacy_grid_to_reference_video), bool(payload.apply_privacy_grid_to_target_identity_image), text_value(payload.reference_privacy_mode) or DEFAULT_REFERENCE_PRIVACY_MODE),
            "target_video_seconds": payload.target_video_seconds,
            "minimum_video_seconds": payload.minimum_video_seconds,
            "face_detections_manifest": text_value(payload.face_detections_manifest),
            "block_on_face_not_detected": bool(payload.block_on_face_not_detected),
        }

    def create_dance_mimic_task_from_paths(payload: DanceMimicTaskCreatePayload, source: Path | None, target_identity: Path | None, created: int | None = None, session_id: int | None = None, workspace: Path | None = None) -> dict[str, Any]:
        if payload.target_video_seconds < payload.minimum_video_seconds:
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_split_config_invalid", "message": "target_video_seconds must be >= minimum_video_seconds."})
        if payload.auto_run and (not source or not target_identity):
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_media_required_for_run", "message": "Reference video and target image are required to run."})
        created = created or now_ms()
        title = session_title(payload)
        if session_id is None or workspace is None:
            session_id = ctx.session_repo.create(
                source=OPENCLIP_SOURCE,
                group_id=OPENCLIP_GROUP_ID,
                sender_name="动作模拟",
                title=title,
                command_text="",
                status="draft",
                workspace_dir=str(ctx.workspace_store.sessions_root() / "pending" / str(created) / "workspace"),
                share_token=ctx.new_share_token(),
                created_at=created,
                updated_at=created,
            )
            workspace = ctx.workspace_store.create_session_workspace(session_id)
            ctx.session_repo.update(session_id, workspace_dir=str(workspace), updated_at=created)
        dance_config = dance_config_from_payload(payload, source, target_identity, workspace)
        task_id = repo.create_task(
            session_id=session_id,
            status="draft",
            workflow_mode=WORKFLOW_DANCE_MIMIC_V1,
            reference_video_path=dance_config["reference_video_path"],
            industry="",
            persona="",
            target_audience="",
            product_info="",
            constraints="",
            analysis_goal="DanceMimic reference motion mimic",
            video_formula="dance_mimic_motion_reference",
            simple_prompt="",
            final_prompt="",
            rewrite_simple_prompt="",
            rewrite_final_prompt="",
            storyboard_simple_prompt="",
            storyboard_final_prompt="",
            storyboard_quick_config_json=json.dumps(dance_config, ensure_ascii=False, sort_keys=True),
            prompt_model_provider="",
            prompt_model_id="",
            run_model_provider=OPENROUTER_PROVIDER,
            run_model_id=OPENROUTER_SEEDANCE_MODEL,
            created_at=created,
            updated_at=created,
        )
        if not payload.title.strip():
            title = default_session_task_title(task_id, int(session_id))
            ctx.session_repo.update(int(session_id), title=title, updated_at=created)
        meta = {
            "schema_version": "koubo_task_list_meta_0.1",
            "workflow_id": WORKFLOW_DANCE_MIMIC_V1,
            "workflow_mode": WORKFLOW_DANCE_MIMIC_V1,
            "task_id": task_id,
            "session_id": session_id,
            "title": title,
            "create_mode": "dance_mimic",
            "input_mode": "reference_video",
            "reference_video_path": dance_config["reference_video_path"],
            "target_identity_image_path": dance_config["target_identity_image_path"],
            "reference_privacy_mode": dance_config["reference_privacy_mode"],
            "apply_privacy_grid_to_reference_video": dance_config["apply_privacy_grid_to_reference_video"],
            "apply_privacy_grid_to_target_identity_image": dance_config["apply_privacy_grid_to_target_identity_image"],
            "effective_grid_scope": dance_config["effective_grid_scope"],
            "dance_mimic": dance_config,
            "created_at": created,
            "updated_at": created,
            "initialize_status": "draft",
        }
        write_json(workspace / TASK_META_REL, meta)
        add_event(int(session_id), "dance_mimic_v1.task.created", {"task_id": task_id, "workspace_dir": str(workspace), "reference_video_path": dance_config["reference_video_path"], "target_identity_image_path": dance_config["target_identity_image_path"]}, task_id=task_id)
        response: dict[str, Any] = {
            "ok": True,
            "task_id": task_id,
            "session_id": session_id,
            "workspace_dir": str(workspace),
            "workflow_mode": WORKFLOW_DANCE_MIMIC_V1,
            "item": serialize_item(task_id),
        }
        if payload.auto_run:
            response["run"] = start_run(task_id, normalize_run_payload(payload))
        return response

    def update_dance_mimic_task_from_paths(task_id: int, payload: DanceMimicTaskCreatePayload, source: Path | None, target_identity: Path | None) -> dict[str, Any]:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        if payload.target_video_seconds < payload.minimum_video_seconds:
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_split_config_invalid", "message": "target_video_seconds must be >= minimum_video_seconds."})
        if payload.auto_run and (not source or not target_identity):
            raise HTTPException(status_code=400, detail={"code": "dance_mimic_media_required_for_run", "message": "Reference video and target image are required to run."})
        updated = now_ms()
        session_id = int(task["session_id"])
        workspace = workspace_for(task)
        title = payload.title.strip() or default_session_task_title(task_id, session_id)
        previous_config = json_dict(task.get("storyboard_quick_config_json"))
        dance_config = dance_config_from_payload(payload, source, target_identity, workspace)
        preprocessing_keys = (
            "reference_video_path",
            "target_identity_image_path",
            "reference_privacy_mode",
            "apply_privacy_grid_to_reference_video",
            "apply_privacy_grid_to_target_identity_image",
            "target_video_seconds",
            "minimum_video_seconds",
            "face_detections_manifest",
            "block_on_face_not_detected",
        )
        previous_preprocessing_config = {
            "reference_video_path": text_value(previous_config.get("reference_video_path")),
            "target_identity_image_path": text_value(previous_config.get("target_identity_image_path")),
            "reference_privacy_mode": text_value(previous_config.get("reference_privacy_mode")) or DEFAULT_REFERENCE_PRIVACY_MODE,
            "apply_privacy_grid_to_reference_video": bool(previous_config.get("apply_privacy_grid_to_reference_video", True)),
            "apply_privacy_grid_to_target_identity_image": bool(previous_config.get("apply_privacy_grid_to_target_identity_image", True)),
            "target_video_seconds": safe_float(previous_config.get("target_video_seconds"), 8.0),
            "minimum_video_seconds": safe_float(previous_config.get("minimum_video_seconds"), 4.0),
            "face_detections_manifest": text_value(previous_config.get("face_detections_manifest")),
            "block_on_face_not_detected": bool(previous_config.get("block_on_face_not_detected", True)),
        }
        preprocessing_config_changed = any(previous_preprocessing_config[key] != dance_config.get(key) for key in preprocessing_keys)
        repo.update_task(task_id, reference_video_path=dance_config["reference_video_path"], storyboard_quick_config_json=json.dumps(dance_config, ensure_ascii=False, sort_keys=True), updated_at=updated)
        ctx.session_repo.update(session_id, title=title, updated_at=updated)
        meta_path = workspace / TASK_META_REL
        meta = read_json(meta_path)
        meta.update({
            "workflow_id": WORKFLOW_DANCE_MIMIC_V1,
            "workflow_mode": WORKFLOW_DANCE_MIMIC_V1,
            "task_id": task_id,
            "session_id": session_id,
            "title": title,
            "create_mode": "dance_mimic",
            "input_mode": "reference_video",
            "reference_video_path": dance_config["reference_video_path"],
            "target_identity_image_path": dance_config["target_identity_image_path"],
            "reference_privacy_mode": dance_config["reference_privacy_mode"],
            "apply_privacy_grid_to_reference_video": dance_config["apply_privacy_grid_to_reference_video"],
            "apply_privacy_grid_to_target_identity_image": dance_config["apply_privacy_grid_to_target_identity_image"],
            "effective_grid_scope": dance_config["effective_grid_scope"],
            "dance_mimic": dance_config,
            "updated_at": updated,
        })
        write_json(meta_path, meta)
        if preprocessing_config_changed:
            mark_dance_mimic_stale_items(
                workspace,
                {
                    "02_reference_face_masked_video_build": ["SessionOutput/reference/privacy_grid_manifest.json", "SessionOutput/reference/segments"],
                    "03_storyboard_standard_task_build": [STORYBOARD_REL, STORYBOARD_SEED_REL],
                    "video_generation_plan": ["SessionOutput/storyboard/video_generation_plan.json", "SessionOutput/storyboard/video_plan_execution_result.json"],
                    "video_only_generation_plan": ["SessionOutput/storyboard/video_only_generation_plan.json", "SessionOutput/storyboard/video_only_plan_execution_result.json"],
                },
                source_step="dance_mimic_task_update",
                reason="dance_mimic_preprocessing_config_changed",
            )
        add_event(session_id, "dance_mimic_v1.task.updated", {"task_id": task_id, "workspace_dir": str(workspace), "reference_video_path": dance_config["reference_video_path"], "target_identity_image_path": dance_config["target_identity_image_path"]}, task_id=task_id)
        response: dict[str, Any] = {"ok": True, "task_id": task_id, "session_id": session_id, "workspace_dir": str(workspace), "workflow_mode": WORKFLOW_DANCE_MIMIC_V1, "item": serialize_item(task_id)}
        if payload.auto_run:
            response["run"] = start_run(task_id, normalize_run_payload(payload))
        return response

    @router.post("/api/dance-mimic-v1/tasks/with-uploads")
    async def create_dance_mimic_task_with_uploads(
        title: str = Form(""),
        reference_video_path: str = Form(""),
        target_identity_image_path: str = Form(""),
        target_video_seconds: float = Form(8.0),
        minimum_video_seconds: float = Form(4.0),
        face_detections_manifest: str = Form(""),
        reference_privacy_mode: str = Form(DEFAULT_REFERENCE_PRIVACY_MODE),
        apply_privacy_grid_to_reference_video: bool = Form(True),
        apply_privacy_grid_to_target_identity_image: bool = Form(True),
        block_on_face_not_detected: bool = Form(True),
        auto_run: bool = Form(False),
        reference_video_file: Optional[UploadFile] = File(default=None),
        target_identity_image_file: Optional[UploadFile] = File(default=None),
    ) -> dict[str, Any]:
        created = now_ms()
        title_seed = title.strip() or "动作模拟任务"
        session_id = ctx.session_repo.create(source=OPENCLIP_SOURCE, group_id=OPENCLIP_GROUP_ID, sender_name="动作模拟", title=title_seed, command_text="", status="draft", workspace_dir=str(ctx.workspace_store.sessions_root() / "pending" / str(created) / "workspace"), share_token=ctx.new_share_token(), created_at=created, updated_at=created)
        workspace = ctx.workspace_store.create_session_workspace(session_id)
        ctx.session_repo.update(session_id, workspace_dir=str(workspace), updated_at=created)
        source = await save_upload_file_to_session_context(reference_video_file, workspace=workspace, target_rel=SESSION_CONTEXT_REFERENCE_VIDEO_REL, fallback="reference.mp4", supported_exts=SUPPORTED_REFERENCE_VIDEO_EXTS, unsupported_code="dance_mimic_reference_video_unsupported", empty_code="dance_mimic_reference_video_empty", label="reference video") if reference_video_file else (validate_reference_video(reference_video_path, workspace=workspace) if auto_run else (path_in_workspace(workspace, reference_video_path) if text_value(reference_video_path) else None))
        target_identity = await save_upload_file_to_session_context(target_identity_image_file, workspace=workspace, target_rel_prefix=TARGET_IDENTITY_IMAGE_REL_PREFIX, fallback="target_identity.png", supported_exts=SUPPORTED_TARGET_IMAGE_EXTS, unsupported_code="dance_mimic_target_identity_image_unsupported", empty_code="dance_mimic_target_identity_image_empty", label="target identity image") if target_identity_image_file else (validate_target_identity_image(target_identity_image_path, workspace=workspace) if auto_run else (path_in_workspace(workspace, target_identity_image_path) if text_value(target_identity_image_path) else None))
        payload = DanceMimicTaskCreatePayload(title=title.strip(), reference_video_path=str(source) if source else "", target_identity_image_path=str(target_identity) if target_identity else "", target_video_seconds=target_video_seconds, minimum_video_seconds=minimum_video_seconds, face_detections_manifest=face_detections_manifest, reference_privacy_mode=reference_privacy_mode, apply_privacy_grid_to_reference_video=apply_privacy_grid_to_reference_video, apply_privacy_grid_to_target_identity_image=apply_privacy_grid_to_target_identity_image, block_on_face_not_detected=block_on_face_not_detected, auto_run=auto_run)
        return create_dance_mimic_task_from_paths(payload, source, target_identity, created=created, session_id=session_id, workspace=workspace)

    @router.put("/api/dance-mimic-v1/tasks/{task_id}/with-uploads")
    async def update_dance_mimic_task_with_uploads(
        task_id: int,
        title: str = Form(""),
        reference_video_path: str = Form(""),
        target_identity_image_path: str = Form(""),
        target_video_seconds: float = Form(8.0),
        minimum_video_seconds: float = Form(4.0),
        face_detections_manifest: str = Form(""),
        reference_privacy_mode: str = Form(DEFAULT_REFERENCE_PRIVACY_MODE),
        apply_privacy_grid_to_reference_video: bool = Form(True),
        apply_privacy_grid_to_target_identity_image: bool = Form(True),
        block_on_face_not_detected: bool = Form(True),
        auto_run: bool = Form(False),
        reference_video_file: Optional[UploadFile] = File(default=None),
        target_identity_image_file: Optional[UploadFile] = File(default=None),
    ) -> dict[str, Any]:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        workspace = workspace_for(task)
        source = await save_upload_file_to_session_context(reference_video_file, workspace=workspace, target_rel=SESSION_CONTEXT_REFERENCE_VIDEO_REL, fallback="reference.mp4", supported_exts=SUPPORTED_REFERENCE_VIDEO_EXTS, unsupported_code="dance_mimic_reference_video_unsupported", empty_code="dance_mimic_reference_video_empty", label="reference video") if reference_video_file else (validate_reference_video(reference_video_path, workspace=workspace) if auto_run else (path_in_workspace(workspace, reference_video_path) if text_value(reference_video_path) else None))
        target_identity = await save_upload_file_to_session_context(target_identity_image_file, workspace=workspace, target_rel_prefix=TARGET_IDENTITY_IMAGE_REL_PREFIX, fallback="target_identity.png", supported_exts=SUPPORTED_TARGET_IMAGE_EXTS, unsupported_code="dance_mimic_target_identity_image_unsupported", empty_code="dance_mimic_target_identity_image_empty", label="target identity image") if target_identity_image_file else (validate_target_identity_image(target_identity_image_path, workspace=workspace) if auto_run else (path_in_workspace(workspace, target_identity_image_path) if text_value(target_identity_image_path) else None))
        payload = DanceMimicTaskCreatePayload(title=title.strip(), reference_video_path=str(source) if source else "", target_identity_image_path=str(target_identity) if target_identity else "", target_video_seconds=target_video_seconds, minimum_video_seconds=minimum_video_seconds, face_detections_manifest=face_detections_manifest, reference_privacy_mode=reference_privacy_mode, apply_privacy_grid_to_reference_video=apply_privacy_grid_to_reference_video, apply_privacy_grid_to_target_identity_image=apply_privacy_grid_to_target_identity_image, block_on_face_not_detected=block_on_face_not_detected, auto_run=auto_run)
        return update_dance_mimic_task_from_paths(task_id, payload, source, target_identity)

    @router.post("/api/dance-mimic-v1/tasks")
    async def create_dance_mimic_task(payload: DanceMimicTaskCreatePayload) -> dict[str, Any]:
        source = validate_reference_video(payload.reference_video_path) if payload.auto_run else (Path(payload.reference_video_path).expanduser() if text_value(payload.reference_video_path) else None)
        target_identity = validate_target_identity_image(payload.target_identity_image_path) if payload.auto_run else (Path(payload.target_identity_image_path).expanduser() if text_value(payload.target_identity_image_path) else None)
        return create_dance_mimic_task_from_paths(payload, source, target_identity)

    @router.get("/api/dance-mimic-v1/tasks/{task_id}/privacy-grid-preview/reference")
    async def preview_task_reference_privacy_grid(task_id: int) -> FileResponse:
        target, sha256 = privacy_grid_preview_file(task_id, "reference")
        return FileResponse(
            target,
            media_type=target_image_media_type(target),
            headers={"Cache-Control": "private, max-age=3600", "ETag": f'"{sha256}"'},
        )

    @router.get("/api/dance-mimic-v1/tasks/{task_id}/privacy-grid-preview/target")
    async def preview_task_target_privacy_grid(task_id: int) -> FileResponse:
        target, sha256 = privacy_grid_preview_file(task_id, "target")
        return FileResponse(
            target,
            media_type=target_image_media_type(target),
            headers={"Cache-Control": "private, max-age=3600", "ETag": f'"{sha256}"'},
        )

    @router.get("/api/dance-mimic-v1/tasks/{task_id}")
    async def get_dance_mimic_task_detail(task_id: int) -> dict[str, Any]:
        return task_detail(task_id)

    @router.get("/api/dance-mimic-v1/tasks/{task_id}/run/plan")
    async def get_dance_mimic_run_plan(task_id: int) -> dict[str, Any]:
        task = get_task(task_id)
        ensure_dance_mimic_task(task)
        task = normalize_legacy_session_context_inputs(task)
        meta = read_json(workspace_for(task) / TASK_META_REL)
        payload = run_payload_from_meta(meta)
        return {"ok": True, "task_id": task_id, "target": DANCE_MIMIC_TARGET, **compile_plan(payload)}

    @router.post("/api/dance-mimic-v1/tasks/{task_id}/run")
    async def start_dance_mimic_run(task_id: int, payload: DanceMimicRunPayload) -> dict[str, Any]:
        return start_run(task_id, payload)

    @router.get("/api/dance-mimic-v1/tasks/{task_id}/run/{attempt_id}")
    async def get_dance_mimic_run_status(task_id: int, attempt_id: int) -> dict[str, Any]:
        return run_status(task_id, attempt_id)

    @router.post("/api/dance-mimic-v1/tasks/{task_id}/one-click-movie")
    async def start_dance_mimic_one_click_movie(task_id: int, payload: DanceMimicOneClickMoviePayload) -> dict[str, Any]:
        return start_one_click_movie(task_id, payload)

    @router.get("/api/dance-mimic-v1/tasks/{task_id}/one-click-movie")
    async def get_dance_mimic_latest_one_click_movie(task_id: int) -> dict[str, Any]:
        return one_click_movie_status(task_id)

    @router.get("/api/dance-mimic-v1/tasks/{task_id}/one-click-movie/{run_id}")
    async def get_dance_mimic_one_click_movie_status(task_id: int, run_id: str) -> dict[str, Any]:
        return one_click_movie_status(task_id, run_id)

    return router
