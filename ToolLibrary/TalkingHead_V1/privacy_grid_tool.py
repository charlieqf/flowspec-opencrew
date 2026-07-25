from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from OpenCrew.ToolLibrary.Analysis.media_binaries import find_ffmpeg, find_ffprobe, media_env
except Exception:
    ANALYSIS_DIR = Path(__file__).resolve().parents[1] / "Analysis"
    if str(ANALYSIS_DIR) not in sys.path:
        sys.path.insert(0, str(ANALYSIS_DIR))
    from media_binaries import find_ffmpeg, find_ffprobe, media_env  # type: ignore

try:
    from OpenCrew.ToolLibrary.Analysis_V1 import DEFAULT_DATABASE_URL_ENV, DEFAULT_OPENCREW_DATABASE_URL
except Exception:
    DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
    DEFAULT_OPENCREW_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"


TOOLSET_ID = "DanceMimic_V1"
WORKFLOW_ID = "dance_mimic_v1"
SCHEMA_VERSION = "dance_mimic_v1_tool_result_0.1"
DANCE_MIMIC_REFERENCE_VIDEO_ROLE = "dance_mimic_segment_motion_reference"
DANCE_MIMIC_VIDEO_PROVIDER = "openrouter"
DANCE_MIMIC_VIDEO_MODEL = "bytedance/seedance-2.0"
DANCE_MIMIC_VIDEO_MODEL_LABEL = "ByteDance Seedance 2.0"
DANCE_MIMIC_VIDEO_MODEL_ALIAS = "MaxSR2"
DANCE_MIMIC_REFERENCE_MODE = "input_references"
MEDIA_CONFIG_TABLE = "tool_media_provider_configs"
MEDIAPIPE_FACE_DETECTOR_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
MEDIAPIPE_FACE_DETECTOR_CACHE_REL = ".cache/opencrew/dance_mimic_v1/blaze_face_short_range.tflite"

VARIABLES_REL = "SessionContext/Variables.json"
SOURCE_VIDEO_REL = "SessionContext/Video_Reference_Source.mp4"
TARGET_IDENTITY_IMAGE_STEM_REL = "SessionContext/Target_Identity_Image"
REFERENCE_DIR_REL = "SessionOutput/reference"
REFERENCE_MANIFEST_REL = f"{REFERENCE_DIR_REL}/reference_media_manifest.json"
SILENT_VIDEO_REL = f"{REFERENCE_DIR_REL}/Video_Reference_Silent.mp4"
MIXED_AUDIO_REL = f"{REFERENCE_DIR_REL}/Audio_Reference_Mixed.wav"
VOCAL_AUDIO_REL = f"{REFERENCE_DIR_REL}/Audio_Reference_Vocal.wav"
SEGMENTS_DIR_REL = f"{REFERENCE_DIR_REL}/segments"
SEGMENTS_MANIFEST_REL = f"{SEGMENTS_DIR_REL}/reference_segments_manifest.json"
PRIVACY_GRID_MANIFEST_REL = f"{REFERENCE_DIR_REL}/privacy_grid_manifest.json"
PRIVACY_GRID_REFERENCE_PREVIEW_REL = f"{REFERENCE_DIR_REL}/PrivacyGrid_Reference_Preview.png"
PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE = 12
PRIVACY_GRID_MAX_CELL_SIZE_REFERENCE = 48
SESSION_REPORT_DIR_REL = "SessionReport"
DANCE_MIMIC_STALE_MANIFEST_REL = f"{SESSION_REPORT_DIR_REL}/stale_manifest.json"
STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
STORYBOARD_EDIT_REL = "SessionOutput/storyboard/koubo_storyboard_edit.json"
STORYBOARD_SEED_REL = "SessionOutput/storyboard/storyboard_seed.json"
STORYBOARD_WORKING_REL = "SessionOutput/storyboard/Working"
STORYBOARD_ARCHIVE_DIR_REL = "SessionOutput/storyboard/_archive"
STORYBOARD_VIDEO_ASSETS_REL = "SessionOutput/storyboard/assets/videos"
STORYBOARD_AUDIO_ASSETS_REL = "SessionOutput/storyboard/assets/audios"
VIDEO_PLAN_REL = "SessionOutput/storyboard/video_generation_plan.json"
VIDEO_PLAN_EXECUTION_RESULT_REL = "SessionOutput/storyboard/video_plan_execution_result.json"
VIDEO_PLAN_EXECUTION_STATE_REL = "SessionOutput/storyboard/video_plan_execution_state.json"
VIDEO_ONLY_PLAN_REL = "SessionOutput/storyboard/video_only_generation_plan.json"
VIDEO_ONLY_PLAN_EXECUTION_RESULT_REL = "SessionOutput/storyboard/video_only_plan_execution_result.json"
VIDEO_ONLY_PLAN_EXECUTION_STATE_REL = "SessionOutput/storyboard/video_only_plan_execution_state.json"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

TOOL_META = {
    "00": {"tool_id": "00_PrepareSessionVariables", "tool_dir": "S1_00_PrepareSessionVariables"},
    "01": {"tool_id": "01_ReferenceMediaDemux", "tool_dir": "S2_01_ReferenceMediaDemux"},
    "02": {"tool_id": "02_ReferenceFaceMaskedVideoBuild", "tool_dir": "S3_02_ReferenceFaceMaskedVideoBuild"},
    "03": {"tool_id": "03_StoryBoardStandardTaskBuild", "tool_dir": "S4_03_StoryBoardStandardTaskBuild"},
}


class ToolBlocked(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ToolFailed(ToolBlocked):
    pass


@dataclass(frozen=True)
class Args:
    workspace: str
    workflow_id: str
    task_id: int | None
    session_id: int | None
    attempt_id: int | None
    database_url: str
    database_url_env: str
    force: bool
    resume: bool
    print_json: bool
    source_video_path: str
    target_identity_image_path: str
    face_detections_manifest: str
    reference_privacy_mode: str
    apply_privacy_grid_to_reference_video: bool | None
    apply_privacy_grid_to_target_identity_image: bool | None
    target_video_seconds: float | None
    minimum_video_seconds: float | None
    block_on_face_not_detected: bool


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_analysis_prepare_session_module() -> Any:
    module_path = Path(__file__).resolve().parents[1] / "Analysis_V1" / "00_PrepareSessionVariables.py"
    module_name = "opencrew_analysis_v1_prepare_session_variables_for_dance_mimic"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ToolBlocked("analysis_v1_prepare_session_loader_missing", f"Could not load {module_path}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def resolve_database_url(args: Args) -> str:
    env_name = text_value(args.database_url_env or DEFAULT_DATABASE_URL_ENV)
    return text_value(
        args.database_url
        or os.environ.get(env_name, "")
        or os.environ.get(DEFAULT_DATABASE_URL_ENV, "")
        or os.environ.get("DATABASE_URL", "")
        or DEFAULT_OPENCREW_DATABASE_URL
    )


def dance_mimic_video_config_from_row(module: Any, data: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    provider = text_value(data.get("provider") or DANCE_MIMIC_VIDEO_PROVIDER)
    stored_model = text_value(data.get("model"))
    if not provider or not stored_model:
        raise ToolBlocked("default_video_config_missing", f"DanceMimic OpenRouter video config is incomplete in {MEDIA_CONFIG_TABLE}.")
    extra = module.parse_extra_json(data.get("extra_json")) if hasattr(module, "parse_extra_json") else {}
    extra["reference_mode"] = DANCE_MIMIC_REFERENCE_MODE
    api_key_ref = text_value(data.get("api_key_ref") or "video_openrouter_key")
    legacy_key = text_value(data.get("api_key_ciphertext"))
    has_api_key = bool(module.resolve_secret_value(api_key_ref, legacy_key)) if hasattr(module, "resolve_secret_value") else bool(legacy_key)
    warnings: list[dict[str, str]] = []
    if stored_model != DANCE_MIMIC_VIDEO_MODEL:
        warnings.append({
            "code": "dance_mimic_video_model_overridden",
            "message": f"DanceMimic uses {DANCE_MIMIC_VIDEO_MODEL}; stored OpenRouter model {stored_model} is not used for DanceMimic SR2 generation.",
        })
    return {
        "kind": text_value(data.get("kind") or "video"),
        "provider": provider,
        "model": DANCE_MIMIC_VIDEO_MODEL,
        "model_label": DANCE_MIMIC_VIDEO_MODEL_LABEL,
        "model_alias": DANCE_MIMIC_VIDEO_MODEL_ALIAS,
        "enabled": bool(data.get("enabled")),
        "active": bool(data.get("active")),
        "api_key_ref": api_key_ref,
        "has_api_key": has_api_key,
        "source": f"postgres:{MEDIA_CONFIG_TABLE}:provider={DANCE_MIMIC_VIDEO_PROVIDER}",
        "extra": extra,
        "extra_json": extra,
        "updated_at": text_value(data.get("updated_at")),
    }, warnings


def fetch_dance_mimic_default_video_config(args: Args) -> tuple[dict[str, Any], list[dict[str, str]]]:
    database_url = resolve_database_url(args)
    if not database_url:
        raise ToolBlocked("missing_database_url", f"{DEFAULT_DATABASE_URL_ENV} is required to read DanceMimic default video config.")
    module = load_analysis_prepare_session_module()
    conn = None
    try:
        conn = module.postgres_connect(database_url)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
SELECT kind, provider, model, api_key_ciphertext, api_key_ref, enabled, active, updated_at, extra_json
FROM {MEDIA_CONFIG_TABLE}
WHERE kind = %s AND provider = %s AND enabled = true
ORDER BY id ASC
LIMIT 1
""",
                ("video", DANCE_MIMIC_VIDEO_PROVIDER),
            )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
    except Exception as exc:
        code = text_value(getattr(exc, "code", "")) or "default_video_config_database_read_failed"
        raise ToolBlocked(code, f"Could not read DanceMimic OpenRouter video config from database: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()
    if not row:
        raise ToolBlocked(
            "default_video_config_missing",
            f"DanceMimic video model is not configured in database {MEDIA_CONFIG_TABLE} for provider={DANCE_MIMIC_VIDEO_PROVIDER}.",
        )
    data = dict(zip(columns, row))
    return dance_mimic_video_config_from_row(module, data)


def rel(workspace: Path, path: Path | str | None) -> str:
    if path is None:
        return ""
    p = Path(path)
    try:
        return p.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def workspace_path(workspace: Path, rel_path: str) -> Path:
    path = Path(rel_path)
    return path if path.is_absolute() else workspace / path


def image_extension(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if suffix in IMAGE_EXTS else ""


def ensure_dirs(workspace: Path, tool_dir: str, extra: list[str] | None = None) -> list[str]:
    rels = [f"{tool_dir}/Working", f"{tool_dir}/Output", f"{tool_dir}/Report", *(extra or [])]
    for item in rels:
        (workspace / item).mkdir(parents=True, exist_ok=True)
    return rels


def file_fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest()}


def copy_file(workspace: Path, source: Path, target_rel: str, created: list[str]) -> str:
    target = workspace / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    created.append(target_rel)
    return target_rel


def load_stale_manifest(workspace: Path) -> dict[str, Any]:
    path = workspace / DANCE_MIMIC_STALE_MANIFEST_REL
    if not path.exists():
        return {
            "schema_version": "dance_mimic_v1_stale_manifest_0.1",
            "workflow_id": WORKFLOW_ID,
            "items": {},
            "events": [],
        }
    try:
        payload = read_json(path)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "schema_version": "dance_mimic_v1_stale_manifest_0.1",
        "workflow_id": WORKFLOW_ID,
        "items": dict_value(payload.get("items")),
        "events": list_value(payload.get("events")),
    }


def save_stale_manifest(workspace: Path, manifest: dict[str, Any], created: list[str]) -> None:
    manifest["updated_at"] = now_iso()
    write_json(workspace / DANCE_MIMIC_STALE_MANIFEST_REL, manifest)
    if DANCE_MIMIC_STALE_MANIFEST_REL not in created:
        created.append(DANCE_MIMIC_STALE_MANIFEST_REL)


def mark_downstream_stale(
    workspace: Path,
    result: dict[str, Any],
    *,
    source_step: str,
    reason: str,
    items: dict[str, list[str]],
) -> None:
    manifest = load_stale_manifest(workspace)
    timestamp = now_iso()
    item_map = dict_value(manifest.get("items"))
    for item_id, paths in items.items():
        item_map[item_id] = {
            "status": "stale",
            "source_step": source_step,
            "reason": reason,
            "paths": paths,
            "updated_at": timestamp,
        }
    manifest["items"] = item_map
    manifest.setdefault("events", []).append({
        "event": "marked_stale",
        "source_step": source_step,
        "reason": reason,
        "items": sorted(items.keys()),
        "created_at": timestamp,
    })
    save_stale_manifest(workspace, manifest, result["created_files"])
    result.setdefault("warnings", []).append({
        "code": "downstream_marked_stale",
        "message": f"{source_step} force run marked downstream DanceMimic outputs stale.",
        "items": sorted(items.keys()),
        "stale_manifest_path": DANCE_MIMIC_STALE_MANIFEST_REL,
    })


def clear_stale_items(workspace: Path, result: dict[str, Any], item_ids: list[str]) -> None:
    manifest_path = workspace / DANCE_MIMIC_STALE_MANIFEST_REL
    if not manifest_path.exists():
        return
    manifest = load_stale_manifest(workspace)
    item_map = dict_value(manifest.get("items"))
    cleared = [item_id for item_id in item_ids if item_id in item_map]
    if not cleared:
        return
    for item_id in cleared:
        item_map.pop(item_id, None)
    manifest["items"] = item_map
    manifest.setdefault("events", []).append({
        "event": "cleared_stale",
        "source_step": text_value(result.get("tool_id") or result.get("tool")),
        "items": cleared,
        "created_at": now_iso(),
    })
    save_stale_manifest(workspace, manifest, result["created_files"])


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def run_command(command: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout, env=media_env())


def ffprobe_json(path: Path) -> dict[str, Any]:
    try:
        completed = run_command([
            find_ffprobe(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ])
    except Exception as exc:
        raise ToolBlocked("ffprobe_missing", str(exc)) from exc
    if completed.returncode != 0:
        raise ToolBlocked("output_probe_failed", (completed.stderr or completed.stdout or "ffprobe failed")[:2000])
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ToolBlocked("output_probe_failed", f"ffprobe returned invalid JSON: {exc}") from exc


def parse_fraction(value: Any) -> float:
    text = text_value(value)
    if not text:
        return 0.0
    if "/" not in text:
        try:
            return float(text)
        except ValueError:
            return 0.0
    left, right = text.split("/", 1)
    try:
        denominator = float(right)
        return 0.0 if denominator == 0 else float(left) / denominator
    except ValueError:
        return 0.0


def probe_media(path: Path) -> dict[str, Any]:
    payload = ffprobe_json(path)
    streams = list_value(payload.get("streams"))
    video = next((item for item in streams if dict_value(item).get("codec_type") == "video"), {})
    audios = [item for item in streams if dict_value(item).get("codec_type") == "audio"]
    fmt = dict_value(payload.get("format"))
    fps = parse_fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    duration = 0.0
    for candidate in (video.get("duration"), fmt.get("duration")):
        try:
            duration = float(candidate)
            if duration > 0:
                break
        except (TypeError, ValueError):
            pass
    frame_count = 0
    try:
        frame_count = int(float(video.get("nb_frames") or 0))
    except (TypeError, ValueError):
        frame_count = 0
    if frame_count <= 0 and fps > 0 and duration > 0:
        frame_count = max(1, int(round(duration * fps)))
    return {
        "duration": round(duration, 6),
        "fps": fps,
        "frame_count": frame_count,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": bool(audios),
        "audio_stream_count": len(audios),
        "format": fmt,
    }


def ffmpeg_dependency_status() -> dict[str, Any]:
    status: dict[str, Any] = {}
    for name, finder in (("ffmpeg", find_ffmpeg), ("ffprobe", find_ffprobe)):
        try:
            status[name] = {"available": True, "path": finder()}
        except Exception as exc:
            status[name] = {"available": False, "path": "", "error": str(exc)}
    return status


def default_variables() -> dict[str, Any]:
    return {
        "workflow_id": WORKFLOW_ID,
        "source_video_path": SOURCE_VIDEO_REL,
        "target_identity_image_path": "",
        "reference_media_demux": {
            "mixed_audio_sample_rate": 44100,
            "mixed_audio_channels": 2,
            "source_separation_engine": "demucs",
            "source_separation_model": "htdemucs",
            "source_separation_timeout_seconds": 1800,
        },
        "storyboard_split_config": {
            "target_video_seconds": 8.0,
            "minimum_video_seconds": 4.0,
        },
        "reference_face_masked_video_build": {
            "face_detection_engine": "insightface_scrfd",
            "face_detection_samples_per_segment": 9,
            "face_detection_min_confidence": 0.35,
            "insightface_model_name": "buffalo_l",
            "insightface_det_size": [640, 640],
            "insightface_providers": ["CPUExecutionProvider"],
            "mediapipe_download_model": True,
            "mediapipe_min_detection_confidence": 0.35,
            "mediapipe_min_suppression_threshold": 0.3,
            "mask_style": "grid_black",
            "reference_privacy_mode": "provider_safe_outline",
            "privacy_grid": {
                "apply_to_reference_video": True,
                "apply_to_target_identity_image": True,
                "line_color": "#ff1f1f",
                "line_width_reference": 1,
                "cell_size_reference": PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE,
                "face_sample_coverage_ratio_min": 0.98,
                "face_area_coverage_ratio_min": 0.95,
                "region_area_ratio_max": 0.45,
            },
            "mask_expand_ratio": {"left": 0.35, "right": 0.35, "top": 0.60, "bottom": 0.35},
            "mask_min_width_pixels": 32,
            "mask_min_height_pixels": 32,
            "grid_line_color": "#2b2b2b",
            "grid_fill_color": "#000000",
            "grid_line_width": 2,
            "grid_cell_size": 18,
            "provider_safe_pose_min_detection_confidence": 0.35,
            "provider_safe_pose_min_tracking_confidence": 0.35,
            "provider_safe_pose_model_complexity": 1,
            "provider_reference_max_bytes": 49000000,
            "provider_reference_video_crf": 28,
            "provider_reference_video_preset": "veryfast",
            "provider_reference_video_max_height": 1280,
            "block_on_face_not_detected": False,
            "qa_sample_frames_per_segment": 8,
            "grid_black_black_pixel_ratio_min": 0.60,
            "masked_region_diff_mean_min": 15.0,
        },
    }


def load_variables(workspace: Path, *, required: bool = True) -> dict[str, Any]:
    path = workspace / VARIABLES_REL
    if not path.exists():
        if required:
            raise ToolBlocked("variables_missing", f"Missing {VARIABLES_REL}.")
        return {}
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ToolBlocked("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    return payload


def base_result(tool_key: str, workspace: Path, args: Args, prepared: list[str]) -> dict[str, Any]:
    meta = TOOL_META[tool_key]
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": meta["tool_id"],
        "tool_id": meta["tool_id"],
        "tool_version": "0.1.0",
        "toolset_id": TOOLSET_ID,
        "workflow_id": WORKFLOW_ID,
        "status": "completed",
        "task_id": args.task_id,
        "session_id": args.session_id,
        "attempt_id": args.attempt_id,
        "workspace_dir": str(workspace),
        "prepared_directories": prepared,
        "created_files": [],
        "inputs": {},
        "outputs": {},
        "warnings": [],
        "blocked_reasons": [],
        "error": {},
        "resume": bool(args.resume),
        "force": bool(args.force),
        "updated_at": now_iso(),
    }


def finalize_result(tool_key: str, workspace: Path, result: dict[str, Any], *, print_json: bool) -> int:
    result["updated_at"] = now_iso()
    result_path = workspace / TOOL_META[tool_key]["tool_dir"] / "Report" / "Result.json"
    result.setdefault("outputs", {})["result_path"] = rel(workspace, result_path)
    if rel(workspace, result_path) not in result.setdefault("created_files", []):
        result["created_files"].append(rel(workspace, result_path))
    write_json(result_path, result)
    if print_json:
        print(json.dumps(json_safe(result), ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "completed" else (2 if result.get("status") == "blocked" else 1)


def blocked_result(tool_key: str, workspace: Path, args: Args, exc: ToolBlocked, prepared: list[str]) -> dict[str, Any]:
    result = base_result(tool_key, workspace, args, prepared)
    result["status"] = "failed" if isinstance(exc, ToolFailed) else "blocked"
    reason = {"code": exc.code, "message": exc.message, "details": exc.details}
    if result["status"] == "blocked":
        result["blocked_reasons"] = [reason]
    result["error"] = reason
    return result


def require_workflow(args: Args) -> None:
    if args.workflow_id != WORKFLOW_ID:
        raise ToolBlocked("unsupported_workflow", f"DanceMimic tools only support workflow_id={WORKFLOW_ID}.")


def demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def run_00(workspace: Path, args: Args) -> dict[str, Any]:
    require_workflow(args)
    prepared = ensure_dirs(workspace, TOOL_META["00"]["tool_dir"], ["SessionContext"])
    result = base_result("00", workspace, args, prepared)
    variables = load_variables(workspace, required=False)
    source_text = text_value(args.source_video_path or variables.get("source_video_path"))
    if not source_text and (workspace / SOURCE_VIDEO_REL).exists():
        source_text = SOURCE_VIDEO_REL
    if not source_text:
        raise ToolBlocked("missing_source_video_path", "Missing source video path. Pass --source-video-path or stage SessionContext/Video_Reference_Source.mp4.")
    source = workspace_path(workspace, source_text)
    if not source.exists():
        raise ToolBlocked("reference_video_missing", f"Reference video does not exist: {source_text}")
    if not source.is_file():
        raise ToolBlocked("reference_video_not_file", f"Reference video is not a file: {source_text}")

    created = result["created_files"]
    target_rel = SOURCE_VIDEO_REL
    target = workspace / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
        created.append(target_rel)

    target_identity_rel = text_value(variables.get("target_identity_image_path"))
    target_identity_text = text_value(args.target_identity_image_path or target_identity_rel)
    staged_identity_rel = ""
    if target_identity_text:
        target_identity_source = workspace_path(workspace, target_identity_text)
        if not target_identity_source.exists():
            raise ToolBlocked("target_identity_image_missing", f"Target identity image does not exist: {target_identity_text}")
        if not target_identity_source.is_file():
            raise ToolBlocked("target_identity_image_not_file", f"Target identity image is not a file: {target_identity_text}")
        if target_identity_source.stat().st_size <= 0:
            raise ToolBlocked("target_identity_image_empty", f"Target identity image is empty: {target_identity_text}")
        suffix = image_extension(target_identity_source)
        if not suffix:
            raise ToolBlocked("target_identity_image_unsupported", f"Target identity image must be one of: {', '.join(sorted(IMAGE_EXTS))}")
        staged_identity_rel = f"{TARGET_IDENTITY_IMAGE_STEM_REL}{suffix}"
        staged_identity = workspace / staged_identity_rel
        staged_identity.parent.mkdir(parents=True, exist_ok=True)
        if target_identity_source.resolve() != staged_identity.resolve():
            shutil.copy2(target_identity_source, staged_identity)
            created.append(staged_identity_rel)

    merged = default_variables()
    merged.update({key: value for key, value in variables.items() if key != "reference_video_path"})
    for key, value in default_variables().items():
        if isinstance(value, dict):
            merged[key] = {**value, **dict_value(variables.get(key))}
    merged["workflow_id"] = WORKFLOW_ID
    merged["source_video_path"] = target_rel
    if staged_identity_rel:
        merged["target_identity_image_path"] = staged_identity_rel
    privacy_mode = text_value(args.reference_privacy_mode)
    if privacy_mode:
        merged.setdefault("reference_face_masked_video_build", {})
        if not isinstance(merged["reference_face_masked_video_build"], dict):
            merged["reference_face_masked_video_build"] = {}
        merged["reference_face_masked_video_build"]["reference_privacy_mode"] = privacy_mode
    privacy_grid = dict_value(dict_value(merged.get("reference_face_masked_video_build")).get("privacy_grid"))
    if args.apply_privacy_grid_to_reference_video is not None:
        privacy_grid["apply_to_reference_video"] = bool(args.apply_privacy_grid_to_reference_video)
    if args.apply_privacy_grid_to_target_identity_image is not None:
        privacy_grid["apply_to_target_identity_image"] = bool(args.apply_privacy_grid_to_target_identity_image)
    merged.setdefault("reference_face_masked_video_build", {})["privacy_grid"] = privacy_grid
    default_video_config, default_video_warnings = fetch_dance_mimic_default_video_config(args)
    merged["default_video_config"] = default_video_config
    result["warnings"].extend(default_video_warnings)
    write_json(workspace / VARIABLES_REL, merged)
    created.append(VARIABLES_REL)
    write_json(workspace / TOOL_META["00"]["tool_dir"] / "Working" / "InputFrom_task_reference.json", {
        "source_arg": source_text,
        "staged_source_video_path": target_rel,
        "target_identity_arg": target_identity_text,
        "staged_target_identity_image_path": staged_identity_rel,
    })
    result["inputs"] = {"source_video": source_text, "target_identity_image": target_identity_text}
    result["outputs"] = {"variables": VARIABLES_REL, "source_video": target_rel, "target_identity_image": staged_identity_rel}
    return result


def export_silent_video(source: Path, target: Path, warnings: list[dict[str, Any]]) -> None:
    try:
        command = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-map", "0:v:0", "-an", "-c:v", "copy", str(target)]
    except Exception as exc:
        raise ToolBlocked("ffmpeg_missing", str(exc)) from exc
    completed = run_command(command)
    if completed.returncode == 0 and target.exists() and target.stat().st_size > 0:
        return
    fallback = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-movflags", "+faststart", str(target)]
    completed = run_command(fallback)
    if completed.returncode != 0 or not target.exists() or target.stat().st_size <= 0:
        raise ToolBlocked("video_silent_export_failed", (completed.stderr or completed.stdout or "ffmpeg failed")[:2000])
    warnings.append({"code": "video_silent_reencoded", "message": "ffmpeg stream copy failed; silent video was re-encoded."})


def extract_mixed_audio(source: Path, target: Path, sample_rate: int, channels: int) -> None:
    try:
        command = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn", "-ac", str(channels), "-ar", str(sample_rate), str(target)]
    except Exception as exc:
        raise ToolBlocked("ffmpeg_missing", str(exc)) from exc
    completed = run_command(command)
    if completed.returncode != 0:
        raise ToolBlocked("mixed_audio_extraction_failed", (completed.stderr or completed.stdout or "ffmpeg failed")[:2000])
    if not target.exists() or target.stat().st_size <= 0:
        raise ToolBlocked("mixed_audio_empty", f"Mixed audio output is empty: {target}")


def generate_silent_mixed_audio(target: Path, duration_seconds: float, sample_rate: int, channels: int) -> None:
    duration = max(0.1, float(duration_seconds or 0.0))
    layout = "mono" if int(channels) == 1 else "stereo"
    try:
        command = [
            find_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout={layout}:sample_rate={int(sample_rate)}",
            "-t",
            f"{duration:.3f}",
            "-ac",
            str(int(channels)),
            "-ar",
            str(int(sample_rate)),
            str(target),
        ]
    except Exception as exc:
        raise ToolBlocked("ffmpeg_missing", str(exc)) from exc
    completed = run_command(command)
    if completed.returncode != 0:
        raise ToolBlocked("silent_mixed_audio_generation_failed", (completed.stderr or completed.stdout or "ffmpeg failed")[:2000])
    if not target.exists() or target.stat().st_size <= 0:
        raise ToolBlocked("silent_mixed_audio_empty", f"Silent mixed audio output is empty: {target}")


def extract_audio_segment(source: Path, target: Path, start_seconds: float, duration_seconds: float) -> None:
    duration = max(0.1, float(duration_seconds or 0.0))
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        command = [
            find_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, float(start_seconds or 0.0)):.6f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.6f}",
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(target),
        ]
    except Exception as exc:
        raise ToolBlocked("ffmpeg_missing", str(exc)) from exc
    completed = run_command(command)
    if completed.returncode != 0:
        raise ToolBlocked("segment_audio_extraction_failed", (completed.stderr or completed.stdout or "ffmpeg failed")[:2000])
    if not target.exists() or target.stat().st_size <= 0:
        raise ToolBlocked("segment_audio_empty", f"Segment audio output is empty: {target}")


def run_01(workspace: Path, args: Args) -> dict[str, Any]:
    require_workflow(args)
    prepared = ensure_dirs(workspace, TOOL_META["01"]["tool_dir"], [REFERENCE_DIR_REL])
    result = base_result("01", workspace, args, prepared)
    variables = load_variables(workspace)
    source_rel = text_value(variables.get("source_video_path"))
    if not source_rel:
        raise ToolBlocked("missing_source_video_path", f"{VARIABLES_REL}.source_video_path is required.")
    source = workspace_path(workspace, source_rel)
    if not source.exists():
        raise ToolBlocked("reference_video_missing", source_rel)
    probe = probe_media(source)

    created = result["created_files"]
    working_source = workspace / TOOL_META["01"]["tool_dir"] / "Working" / "Input_Video_Reference_Source.mp4"
    shutil.copy2(source, working_source)
    write_json(workspace / TOOL_META["01"]["tool_dir"] / "Working" / "InputFrom_0_Variables.json", variables)
    created.extend([rel(workspace, working_source), f"{TOOL_META['01']['tool_dir']}/Working/InputFrom_0_Variables.json"])

    config = {**dict_value(default_variables()["reference_media_demux"]), **dict_value(variables.get("reference_media_demux"))}
    silent = workspace / SILENT_VIDEO_REL
    mixed = workspace / MIXED_AUDIO_REL
    vocal = workspace / VOCAL_AUDIO_REL
    for path in (silent, mixed, vocal):
        if args.force:
            remove_path(path)
    warnings = result["warnings"]
    export_silent_video(source, silent, warnings)
    silent_probe = probe_media(silent)
    if silent_probe["has_audio"]:
        raise ToolBlocked("video_silent_export_failed", "Silent video still has an audio stream.")
    mixed_audio_source = "source_audio"
    if probe["has_audio"]:
        extract_mixed_audio(source, mixed, int(config["mixed_audio_sample_rate"]), int(config["mixed_audio_channels"]))
    else:
        generate_silent_mixed_audio(mixed, float_value(probe.get("duration"), 0.0), int(config["mixed_audio_sample_rate"]), int(config["mixed_audio_channels"]))
        mixed_audio_source = "generated_silence"
        warnings.append({"code": "source_audio_missing_silent_mixed_audio", "message": "Reference video has no audio stream; generated silent mixed audio for DanceMimic downstream compatibility."})
    mixed_probe = probe_media(mixed)

    vocal_probe: dict[str, Any] = {}
    if demucs_available() and bool(config.get("run_demucs", False)):
        warnings.append({"code": "vocal_audio_skipped", "message": "Demucs execution is not enabled in this MVP; mixed audio remains the default downstream audio."})
    else:
        warnings.append({"code": "vocal_audio_skipped_demucs_unavailable", "message": "Demucs is unavailable or disabled; vocal audio is optional and was skipped."})
        if vocal.exists():
            remove_path(vocal)

    for path in (silent, mixed):
        created.append(rel(workspace, path))
    manifest = {
        "schema_version": "dance_mimic_v1_reference_media_demux_0.1",
        "tool": TOOL_META["01"]["tool_id"],
        "workflow_id": WORKFLOW_ID,
        "source_video": source_rel,
        "source_fingerprint": file_fingerprint(source),
        "media_dependencies": ffmpeg_dependency_status() | {"demucs": {"available": demucs_available(), "model": text_value(config.get("source_separation_model") or "htdemucs")}},
        "outputs": {
            "silent_video": SILENT_VIDEO_REL,
            "mixed_audio": MIXED_AUDIO_REL,
            "vocal_audio": VOCAL_AUDIO_REL if vocal.exists() and vocal.stat().st_size > 0 else "",
        },
        "audio_config": {
            "mixed_audio_sample_rate": int(config["mixed_audio_sample_rate"]),
            "mixed_audio_channels": int(config["mixed_audio_channels"]),
            "mixed_audio_source": mixed_audio_source,
        },
        "source_separation": {
            "engine": text_value(config.get("source_separation_engine") or "demucs"),
            "model": text_value(config.get("source_separation_model") or "htdemucs"),
            "optional": True,
        },
        "probes": {"source_video": probe, "silent_video": silent_probe, "mixed_audio": mixed_probe, "vocal_audio": vocal_probe},
        "warnings": warnings,
        "created_at": now_iso(),
    }
    tool_manifest_rel = f"{TOOL_META['01']['tool_dir']}/Output/reference_media_demux_manifest.json"
    write_json(workspace / tool_manifest_rel, manifest)
    write_json(workspace / REFERENCE_MANIFEST_REL, manifest)
    created.extend([tool_manifest_rel, REFERENCE_MANIFEST_REL])
    result["inputs"] = {"variables": VARIABLES_REL, "source_video": source_rel}
    result["outputs"] = {"silent_video": SILENT_VIDEO_REL, "mixed_audio": MIXED_AUDIO_REL, "vocal_audio": manifest["outputs"]["vocal_audio"], "manifest": tool_manifest_rel, "session_manifest": REFERENCE_MANIFEST_REL}
    if args.force:
        mark_downstream_stale(
            workspace,
            result,
            source_step="01_ReferenceMediaDemux",
            reason="reference_media_force_rerun",
            items={
                "02_reference_face_masked_video_build": [SEGMENTS_MANIFEST_REL, SEGMENTS_DIR_REL],
                "03_storyboard_standard_task_build": [STORYBOARD_REL, STORYBOARD_SEED_REL, STORYBOARD_VIDEO_ASSETS_REL],
            },
        )
    return result


def import_cv2_np() -> tuple[Any, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:
        raise ToolBlocked("opencv_missing", "OpenCV and numpy are required for fixed-bbox face masking fixtures.") from exc
    return cv2, np


def int_value(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def positive_int(value: Any, default: int) -> int:
    number = int_value(value, default)
    return number if number > 0 else default


def median_number(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def bbox_area_ratio(bbox: list[int], width: int, height: int) -> float:
    if width <= 0 or height <= 0:
        return 0.0
    return (float(bbox[2]) * float(bbox[3])) / float(width * height)


def normalize_detected_bbox(values: Any, width: int, height: int) -> list[int] | None:
    raw = list_value(values)
    if len(raw) < 4:
        return None
    try:
        x, y, w, h = [int(round(float(v))) for v in raw[:4]]
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0 or width <= 0 or height <= 0:
        return None
    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    w = min(width - x, w)
    h = min(height - y, h)
    if w <= 0 or h <= 0:
        return None
    return [x, y, w, h]


def segment_sample_frame_indices(segment: dict[str, Any], sample_count: int) -> list[int]:
    start = int_value(segment.get("start_frame"), 0)
    end = int_value(segment.get("end_frame"), start)
    if end < start:
        end = start
    total = end - start + 1
    count = max(1, min(sample_count, total))
    if count == 1:
        return [start + total // 2]
    indices = []
    for offset in range(count):
        frame = int(round(start + (end - start) * (offset / float(count - 1))))
        if frame not in indices:
            indices.append(frame)
    return indices


def normalized_face_candidates(candidates: list[dict[str, Any]], width: int, height: int, *, min_confidence: float = 0.0) -> list[dict[str, Any]]:
    normalized = []
    for candidate in candidates:
        bbox = normalize_detected_bbox(candidate.get("bbox"), width, height)
        if not bbox:
            continue
        area = bbox_area_ratio(bbox, width, height)
        center_y = (bbox[1] + bbox[3] / 2.0) / float(height) if height > 0 else 0.0
        center_x = (bbox[0] + bbox[2] / 2.0) / float(width) if width > 0 else 0.0
        if area < 0.0002 or area > 0.14:
            continue
        if center_y > 0.72:
            continue
        if center_x < -0.02 or center_x > 1.02:
            continue
        if float_value(candidate.get("confidence"), 0.0) < min_confidence:
            continue
        normalized.append({
            **candidate,
            "bbox": bbox,
            "area_ratio": area,
            "center_y_ratio": center_y,
        })
    return normalized


def select_detected_face(candidates: list[dict[str, Any]], width: int, height: int, *, engine: str) -> dict[str, Any] | None:
    normalized = normalized_face_candidates(candidates, width, height)
    if not normalized:
        return None
    if engine == "opencv_haar":
        return max(normalized, key=lambda item: (float_value(item.get("confidence")), float(item.get("area_ratio") or 0.0)))
    return max(normalized, key=lambda item: (float_value(item.get("confidence")), float(item.get("area_ratio") or 0.0)))


def aggregate_sample_bboxes(samples: list[dict[str, Any]], width: int, height: int) -> list[int] | None:
    boxes = [normalize_detected_bbox(item.get("bbox"), width, height) for item in samples]
    boxes = [box for box in boxes if box]
    if not boxes:
        return None
    x = int(round(median_number([float(box[0]) for box in boxes])))
    y = int(round(median_number([float(box[1]) for box in boxes])))
    w = int(round(median_number([float(box[2]) for box in boxes])))
    h = int(round(median_number([float(box[3]) for box in boxes])))
    return normalize_detected_bbox([x, y, w, h], width, height)


def detection_item_for_segment(detections: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    for item in list_value(detections.get("segments")):
        if not isinstance(item, dict):
            continue
        if text_value(item.get("segment_id")) == segment["segment_id"] or int(item.get("index") or 0) == int(segment["index"]):
            return item
    return {}


def sample_track_for_segment(detections: dict[str, Any], segment: dict[str, Any], width: int, height: int) -> list[dict[str, Any]]:
    item = detection_item_for_segment(detections, segment)
    samples = []
    for sample in list_value(item.get("samples")):
        if not isinstance(sample, dict):
            continue
        bbox = normalize_detected_bbox(sample.get("bbox"), width, height)
        if not bbox:
            continue
        samples.append({
            "frame_index": int_value(sample.get("frame_index"), int_value(segment.get("start_frame"), 0)),
            "bbox": bbox,
            "confidence": float_value(sample.get("confidence"), 0.0),
            "engine": text_value(sample.get("engine") or detections.get("face_detection_engine")),
        })
    return sorted(samples, key=lambda item: int_value(item.get("frame_index"), 0))


def interpolated_track_bbox(track: list[dict[str, Any]], default_bbox: list[int] | None, frame_index: int) -> tuple[list[int] | None, float, str]:
    if not track:
        return default_bbox, 1.0 if default_bbox else 0.0, ""
    if len(track) == 1:
        return list_value(track[0].get("bbox")), float_value(track[0].get("confidence"), 0.0), text_value(track[0].get("engine"))
    previous = track[0]
    for current in track[1:]:
        previous_frame = int_value(previous.get("frame_index"), frame_index)
        current_frame = int_value(current.get("frame_index"), frame_index)
        if frame_index <= current_frame:
            if current_frame <= previous_frame:
                return list_value(current.get("bbox")), float_value(current.get("confidence"), 0.0), text_value(current.get("engine"))
            ratio = max(0.0, min(1.0, (frame_index - previous_frame) / float(current_frame - previous_frame)))
            prev_bbox = list_value(previous.get("bbox"))
            curr_bbox = list_value(current.get("bbox"))
            bbox = [int(round(float(prev_bbox[i]) + (float(curr_bbox[i]) - float(prev_bbox[i])) * ratio)) for i in range(4)]
            confidence = float_value(previous.get("confidence"), 0.0) + (float_value(current.get("confidence"), 0.0) - float_value(previous.get("confidence"), 0.0)) * ratio
            return bbox, confidence, text_value(current.get("engine") or previous.get("engine"))
        previous = current
    return list_value(track[-1].get("bbox")), float_value(track[-1].get("confidence"), 0.0), text_value(track[-1].get("engine"))


def real_detector_engines_for_request(engine: str) -> list[str]:
    requested = engine.strip().lower()
    if requested in {"opencv", "opencv_haar", "haar"}:
        return ["opencv_haar"]
    if requested in {"mediapipe", "mediapipe_blazeface", "blazeface"}:
        return ["mediapipe_blazeface", "opencv_haar"]
    if requested in {"insightface", "insightface_scrfd", "scrfd"}:
        return ["insightface_scrfd", "mediapipe_blazeface", "opencv_haar"]
    if requested in {"", "auto"}:
        return ["insightface_scrfd", "mediapipe_blazeface", "opencv_haar"]
    return ["insightface_scrfd", "mediapipe_blazeface", "opencv_haar"]


def insightface_app(config: dict[str, Any]) -> Any:
    try:
        from insightface.app import FaceAnalysis  # type: ignore
    except Exception as exc:
        raise ToolBlocked("insightface_missing", "insightface is not installed.") from exc
    model_name = text_value(config.get("insightface_model_name") or "buffalo_l")
    providers = list_value(config.get("insightface_providers")) or ["CPUExecutionProvider"]
    det_size_value = list_value(config.get("insightface_det_size")) or [640, 640]
    det_size = (positive_int(det_size_value[0] if len(det_size_value) > 0 else 640, 640), positive_int(det_size_value[1] if len(det_size_value) > 1 else 640, 640))
    try:
        app = FaceAnalysis(name=model_name, allowed_modules=["detection"], providers=providers)
        app.prepare(ctx_id=-1, det_size=det_size)
        return app
    except Exception as exc:
        raise ToolBlocked("insightface_detector_unavailable", f"Could not initialize insightface SCRFD detector: {exc}") from exc


def mediapipe_model_path(config: dict[str, Any]) -> Path:
    configured = text_value(config.get("mediapipe_face_detector_model_path"))
    if configured:
        return Path(configured).expanduser()
    return Path.home() / MEDIAPIPE_FACE_DETECTOR_CACHE_REL


def ensure_mediapipe_model(config: dict[str, Any]) -> Path:
    path = mediapipe_model_path(config)
    if path.exists() and path.stat().st_size > 0:
        return path
    if config.get("mediapipe_download_model", True) is False:
        raise ToolBlocked("mediapipe_model_missing", f"Missing MediaPipe face detector model: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(MEDIAPIPE_FACE_DETECTOR_MODEL_URL, timeout=30) as response:
            path.write_bytes(response.read())
    except Exception as exc:
        raise ToolBlocked("mediapipe_model_download_failed", f"Could not download MediaPipe face detector model: {exc}") from exc
    if not path.exists() or path.stat().st_size <= 0:
        raise ToolBlocked("mediapipe_model_missing", f"Downloaded MediaPipe face detector model is empty: {path}")
    return path


def mediapipe_detector(config: dict[str, Any]) -> tuple[Any, Any, Any]:
    try:
        import mediapipe as mp  # type: ignore
        from mediapipe.tasks.python import vision  # type: ignore
        from mediapipe.tasks.python.core.base_options import BaseOptions  # type: ignore
    except Exception as exc:
        raise ToolBlocked("mediapipe_missing", "mediapipe is not installed.") from exc
    model_path = ensure_mediapipe_model(config)
    options = vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        min_detection_confidence=float_value(config.get("mediapipe_min_detection_confidence"), 0.35),
        min_suppression_threshold=float_value(config.get("mediapipe_min_suppression_threshold"), 0.3),
    )
    try:
        return mp, vision, vision.FaceDetector.create_from_options(options)
    except Exception as exc:
        raise ToolBlocked("mediapipe_detector_unavailable", f"Could not initialize MediaPipe face detector: {exc}") from exc


def opencv_haar_cascades(cv2: Any) -> list[tuple[str, Any, float]]:
    cascade_dir = Path(getattr(cv2.data, "haarcascades", ""))
    cascade_names = [
        ("haarcascade_frontalface_default.xml", 0.48),
        ("haarcascade_frontalface_alt2.xml", 0.46),
        ("haarcascade_profileface.xml", 0.42),
    ]
    cascades = []
    for name, confidence in cascade_names:
        path = cascade_dir / name
        if not path.exists():
            continue
        cascade = cv2.CascadeClassifier(str(path))
        if not cascade.empty():
            cascades.append((name, cascade, confidence))
    if not cascades:
        raise ToolBlocked("opencv_haar_cascades_missing", "OpenCV Haar face cascade files are unavailable.")
    return cascades


def detect_faces_for_segments_with_engine(
    workspace: Path,
    source_video: Path,
    segments: list[dict[str, Any]],
    config: dict[str, Any],
    probe: dict[str, Any],
    *,
    requested_engine: str,
    engine: str,
) -> dict[str, Any]:
    cv2, np = import_cv2_np()
    cap = cv2.VideoCapture(str(source_video))
    if not cap.isOpened():
        raise ToolFailed("video_open_failed", f"OpenCV cannot open {source_video}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or float_value(probe.get("fps"), 24.0) or 24.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or int_value(probe.get("width"), 0))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or int_value(probe.get("height"), 0))
    sample_count = positive_int(config.get("face_detection_samples_per_segment") or config.get("qa_sample_frames_per_segment"), 9)
    min_confidence = float_value(config.get("face_detection_min_confidence"), 0.35)
    engine_context: dict[str, Any] = {}
    detector = None
    if engine == "insightface_scrfd":
        detector = insightface_app(config)
    elif engine == "mediapipe_blazeface":
        mp, _vision, detector = mediapipe_detector(config)
        engine_context["mp"] = mp
    elif engine == "opencv_haar":
        engine_context["cascades"] = opencv_haar_cascades(cv2)
    else:
        raise ToolBlocked("face_detection_engine_invalid", f"Unsupported face detection engine: {engine}")
    completed: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        for segment in segments:
            samples = []
            detected_count = 0
            for frame_index in segment_sample_frame_indices(segment, sample_count):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, frame = cap.read()
                if not ok:
                    warnings.append({"code": "face_detection_sample_read_failed", "message": f"Could not read frame {frame_index} for {segment['segment_id']}."})
                    continue
                candidates = []
                if engine == "insightface_scrfd":
                    for face in detector.get(frame):
                        raw_bbox = getattr(face, "bbox", None)
                        if raw_bbox is None:
                            continue
                        x1, y1, x2, y2 = [int(round(float(v))) for v in raw_bbox.tolist()[:4]]
                        candidates.append({
                            "bbox": [x1, y1, max(1, x2 - x1), max(1, y2 - y1)],
                            "confidence": float(getattr(face, "det_score", 0.0) or 0.0),
                            "engine": engine,
                        })
                elif engine == "mediapipe_blazeface":
                    mp = engine_context["mp"]
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
                    detection_result = detector.detect(image)
                    for detection in detection_result.detections:
                        bbox = detection.bounding_box
                        categories = getattr(detection, "categories", []) or []
                        confidence = float(getattr(categories[0], "score", 0.0) if categories else 0.0)
                        candidates.append({
                            "bbox": [bbox.origin_x, bbox.origin_y, bbox.width, bbox.height],
                            "confidence": confidence,
                            "engine": engine,
                        })
                else:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    gray = cv2.equalizeHist(gray)
                    for name, cascade, confidence in engine_context["cascades"]:
                        boxes = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=3, minSize=(20, 20))
                        for x, y, w, h in boxes:
                            candidates.append({"bbox": [x, y, w, h], "confidence": confidence, "engine": engine, "cascade": name})
                        if name == "haarcascade_profileface.xml":
                            flipped = cv2.flip(gray, 1)
                            flipped_boxes = cascade.detectMultiScale(flipped, scaleFactor=1.08, minNeighbors=3, minSize=(20, 20))
                            for x, y, w, h in flipped_boxes:
                                candidates.append({"bbox": [width - int(x) - int(w), y, w, h], "confidence": max(0.0, confidence - 0.02), "engine": engine, "cascade": f"{name}:flipped"})
                selected = select_detected_face(candidates, width, height, engine=engine)
                valid_faces = normalized_face_candidates(candidates, width, height, min_confidence=min_confidence)
                if selected and float_value(selected.get("confidence"), 0.0) >= min_confidence:
                    detected_count += 1
                    bbox = list_value(selected.get("bbox"))
                    samples.append({
                        "frame_index": frame_index,
                        "timestamp_seconds": round(frame_index / fps if fps else 0.0, 3),
                        "bbox": bbox,
                        "confidence": round(float_value(selected.get("confidence"), 0.0), 4),
                        "engine": engine,
                        "faces": [
                            {
                                "bbox": list_value(face.get("bbox")),
                                "confidence": round(float_value(face.get("confidence"), 0.0), 4),
                                "engine": text_value(face.get("engine") or engine),
                            }
                            for face in valid_faces
                        ],
                    })
            bbox = aggregate_sample_bboxes(samples, width, height)
            if not bbox:
                warnings.append({"code": "face_not_detected", "message": f"No face detected for {segment['segment_id']} by {engine}."})
            completed.append({
                "segment_id": segment["segment_id"],
                "index": segment["index"],
                "bbox": bbox or [],
                "confidence": round(max((float_value(sample.get("confidence"), 0.0) for sample in samples), default=0.0), 4),
                "sample_count": len(segment_sample_frame_indices(segment, sample_count)),
                "detected_sample_count": detected_count,
                "samples": samples,
            })
    finally:
        cap.release()
        if detector is not None and hasattr(detector, "close"):
            detector.close()
    return {
        "schema_version": "dance_mimic_v1_real_face_detections_0.1",
        "requested_face_detection_engine": requested_engine,
        "face_detection_engine": engine,
        "source_video": rel(workspace, source_video),
        "source_video_probe": probe,
        "sample_config": {"samples_per_segment": sample_count, "min_confidence": min_confidence},
        "segments": completed,
        "warnings": warnings,
        "created_at": now_iso(),
    }


def detect_faces_for_segments(
    workspace: Path,
    source_video: Path,
    segments: list[dict[str, Any]],
    config: dict[str, Any],
    probe: dict[str, Any],
) -> dict[str, Any]:
    requested_engine = text_value(config.get("face_detection_engine") or "insightface_scrfd")
    attempts = []
    for engine in real_detector_engines_for_request(requested_engine):
        try:
            detections = detect_faces_for_segments_with_engine(
                workspace,
                source_video,
                segments,
                config,
                probe,
                requested_engine=requested_engine,
                engine=engine,
            )
            if engine != requested_engine:
                detections.setdefault("warnings", []).append({
                    "code": "face_detection_engine_fallback",
                    "message": f"Requested detector {requested_engine} used fallback {engine}.",
                    "requested_engine": requested_engine,
                    "actual_engine": engine,
                })
            return detections
        except ToolBlocked as exc:
            attempts.append({"engine": engine, "code": exc.code, "message": exc.message})
            continue
    raise ToolBlocked(
        "face_detector_unavailable",
        "No real face detector could be initialized.",
        {"requested_engine": requested_engine, "attempts": attempts},
    )


def detection_face_boxes(detections: dict[str, Any], width: int, height: int) -> list[list[int]]:
    boxes: list[list[int]] = []
    for segment in list_value(detections.get("segments")):
        for sample in list_value(dict_value(segment).get("samples")):
            faces = list_value(dict_value(sample).get("faces"))
            if not faces and dict_value(sample).get("bbox"):
                faces = [sample]
            for face in faces:
                bbox = normalize_detected_bbox(dict_value(face).get("bbox"), width, height)
                if bbox:
                    boxes.append(bbox)
    return boxes


def privacy_grid_region(detections: dict[str, Any], width: int, height: int, config: dict[str, Any]) -> dict[str, Any]:
    _cv2, np = import_cv2_np()
    boxes = detection_face_boxes(detections, width, height)
    if not boxes:
        raise ToolBlocked("privacy_grid_face_not_detected", "Reference video privacy grid requires at least one detected face.")
    values = np.asarray(boxes, dtype=float)
    widths = values[:, 2]
    heights = values[:, 3]
    left = values[:, 0]
    top = values[:, 1]
    right = values[:, 0] + values[:, 2]
    bottom = values[:, 1] + values[:, 3]
    grid = dict_value(config.get("privacy_grid"))
    sample_min = float_value(grid.get("face_sample_coverage_ratio_min"), 0.98)
    area_min = float_value(grid.get("face_area_coverage_ratio_min"), 0.95)
    region_max = float_value(grid.get("region_area_ratio_max"), 0.45)

    def candidate(lower: float, upper: float) -> list[int]:
        margin_w = float(np.median(widths))
        margin_h = float(np.median(heights))
        x1 = max(0.0, float(np.percentile(left, lower)) - 0.15 * margin_w)
        y1 = max(0.0, float(np.percentile(top, lower)) - 0.25 * margin_h)
        x2 = min(float(width), float(np.percentile(right, upper)) + 0.15 * margin_w)
        y2 = min(float(height), float(np.percentile(bottom, upper)) + 0.20 * margin_h)
        return [int(round(x1)), int(round(y1)), max(1, int(round(x2 - x1))), max(1, int(round(y2 - y1)))]

    def metrics(region: list[int]) -> dict[str, float]:
        x, y, w, h = region
        ratios = []
        for bx, by, bw, bh in boxes:
            iw = max(0, min(x + w, bx + bw) - max(x, bx))
            ih = max(0, min(y + h, by + bh) - max(y, by))
            ratios.append((iw * ih) / float(bw * bh))
        return {
            "face_sample_coverage_ratio": sum(value >= 0.95 for value in ratios) / float(len(ratios)),
            "face_area_coverage_ratio": float(np.percentile(ratios, 5)),
            "region_area_ratio": (w * h) / float(width * height),
        }

    region = candidate(1.0, 99.0)
    coverage = metrics(region)
    region_source = "p01_p99"
    if coverage["region_area_ratio"] > region_max:
        raise ToolBlocked("privacy_grid_region_too_large", "Reference video privacy grid region exceeds the 45% frame-area limit.", coverage)
    if coverage["face_sample_coverage_ratio"] < sample_min or coverage["face_area_coverage_ratio"] < area_min:
        region = candidate(0.0, 100.0)
        coverage = metrics(region)
        region_source = "min_max_fallback"
    if coverage["region_area_ratio"] > region_max:
        raise ToolBlocked("privacy_grid_region_too_large", "Reference video privacy grid fallback region exceeds the 45% frame-area limit.", coverage)
    if coverage["face_sample_coverage_ratio"] < sample_min or coverage["face_area_coverage_ratio"] < area_min:
        raise ToolBlocked("privacy_grid_coverage_failed", "Reference video privacy grid region does not meet face coverage thresholds.", coverage)
    x, y, w, h = clamp_bbox(region, width, height)
    return {
        "bbox": [x, y, w, h],
        "normalized_region": {"x1": x / width, "y1": y / height, "x2": (x + w) / width, "y2": (y + h) / height},
        "region_source": region_source,
        "valid_face_sample_count": len(boxes),
        **{key: round(value, 6) for key, value in coverage.items()},
    }


def detect_faces_in_image(image: Any, config: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    cv2, np = import_cv2_np()
    height, width = image.shape[:2]
    min_confidence = float_value(config.get("face_detection_min_confidence"), 0.35)
    attempts = []
    for engine in real_detector_engines_for_request(text_value(config.get("face_detection_engine") or "insightface_scrfd")):
        detector = None
        try:
            candidates: list[dict[str, Any]] = []
            if engine == "insightface_scrfd":
                detector = insightface_app(config)
                for face in detector.get(image):
                    x1, y1, x2, y2 = [int(round(float(v))) for v in face.bbox.tolist()[:4]]
                    candidates.append({"bbox": [x1, y1, max(1, x2 - x1), max(1, y2 - y1)], "confidence": float(face.det_score), "engine": engine})
            elif engine == "mediapipe_blazeface":
                mp, _vision, detector = mediapipe_detector(config)
                rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
                for detection in result.detections:
                    bbox = detection.bounding_box
                    categories = getattr(detection, "categories", []) or []
                    candidates.append({"bbox": [bbox.origin_x, bbox.origin_y, bbox.width, bbox.height], "confidence": float(getattr(categories[0], "score", 0.0) if categories else 0.0), "engine": engine})
            else:
                gray = cv2.equalizeHist(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
                haar_min_neighbors = max(3, int_value(config.get("opencv_haar_min_neighbors"), 3))
                for name, cascade, confidence in opencv_haar_cascades(cv2):
                    for x, y, w, h in cascade.detectMultiScale(
                        gray,
                        scaleFactor=1.08,
                        minNeighbors=haar_min_neighbors,
                        minSize=(20, 20),
                    ):
                        candidates.append({"bbox": [x, y, w, h], "confidence": confidence, "engine": engine, "cascade": name})
            return normalized_face_candidates(candidates, width, height, min_confidence=min_confidence), engine
        except ToolBlocked as exc:
            attempts.append({"engine": engine, "code": exc.code, "message": exc.message})
        finally:
            if detector is not None and hasattr(detector, "close"):
                detector.close()
    raise ToolBlocked("face_detector_unavailable", "No face detector could process the target identity image.", {"attempts": attempts})


def split_segments(duration: float, fps: float, frame_count: int, target: float, minimum: float) -> list[dict[str, Any]]:
    if target < minimum:
        raise ToolBlocked("split_config_invalid", "target_video_seconds must be >= minimum_video_seconds.")
    if duration < minimum:
        raise ToolBlocked("segment_constraints_infeasible", "Source video duration is below minimum_video_seconds.")
    max_segments = max(1, math.floor(duration / minimum))
    segment_count = max(1, math.ceil(duration / target))
    if segment_count > max_segments:
        raise ToolBlocked("segment_constraints_infeasible", "Split constraints cannot satisfy target and minimum segment durations.")
    floor_chunks = math.floor(duration / target)
    remainder = duration - floor_chunks * target
    if remainder <= 0.05:
        boundaries = [(i * target, min(duration, (i + 1) * target)) for i in range(floor_chunks)]
    elif remainder >= minimum:
        boundaries = [(i * target, (i + 1) * target) for i in range(floor_chunks)]
        boundaries.append((floor_chunks * target, duration))
    else:
        even = duration / segment_count
        boundaries = [(i * even, duration if i == segment_count - 1 else (i + 1) * even) for i in range(segment_count)]
    segments = []
    for index, (start, end) in enumerate(boundaries, start=1):
        start_frame = max(0, min(frame_count - 1, int(round(start * fps)))) if fps > 0 else 0
        end_frame_exclusive = max(start_frame + 1, min(frame_count, int(round(end * fps)))) if fps > 0 else frame_count
        segments.append({
            "segment_id": f"segment_{index:04d}",
            "dialogue_asset_key": f"dak_{index:04d}",
            "index": index,
            "start": round(start_frame / fps if fps else start, 3),
            "end": round(end_frame_exclusive / fps if fps else end, 3),
            "duration": round((end_frame_exclusive - start_frame) / fps if fps else end - start, 3),
            "start_frame": start_frame,
            "end_frame": end_frame_exclusive - 1,
            "frame_count": end_frame_exclusive - start_frame,
        })
    return segments


def resolve_detection_manifest(
    workspace: Path,
    args: Args,
    variables: dict[str, Any],
    source_video: Path,
    segments: list[dict[str, Any]],
    config: dict[str, Any],
    probe: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    path_text = text_value(args.face_detections_manifest or dict_value(variables.get("reference_face_masked_video_build")).get("face_detections_manifest"))
    if path_text:
        path = workspace_path(workspace, path_text)
        if not path.exists():
            raise ToolBlocked("face_detections_manifest_missing", path_text)
        payload = read_json(path)
        if not isinstance(payload, dict):
            raise ToolBlocked("face_detections_manifest_invalid", "Detection manifest must be a JSON object.")
        payload.setdefault("source", "provided_manifest")
        return payload
    if text_value(config.get("face_detection_engine")) in {"fake", "fixed_bbox"} and list_value(config.get("fixed_bbox")):
        return {
            "schema_version": "dance_mimic_v1_fake_face_detections_0.1",
            "source": "fixed_bbox_config",
            "face_detection_engine": text_value(config.get("face_detection_engine")),
            "fixed_bbox": config["fixed_bbox"],
            "post_mask_faces": [],
            "warnings": [],
        }
    payload = detect_faces_for_segments(workspace, source_video, segments, config, probe)
    detector_manifest_rel = f"{TOOL_META['02']['tool_dir']}/Output/face_detection_manifest.json"
    write_json(workspace / detector_manifest_rel, payload)
    result["created_files"].append(detector_manifest_rel)
    result["warnings"].extend(list_value(payload.get("warnings")))
    return payload


def bbox_values_from_detection(item: dict[str, Any]) -> tuple[bool, list[Any]]:
    if "bbox" in item:
        values = list_value(item.get("bbox"))
        if not values and ("detected_sample_count" in item or "sample_count" in item):
            return False, []
        return True, values
    if "fixed_bbox" in item:
        return True, list_value(item.get("fixed_bbox"))
    return False, []


def raw_bbox_for_segment(detections: dict[str, Any], segment: dict[str, Any]) -> tuple[bool, list[Any]]:
    for item in list_value(detections.get("segments")):
        if not isinstance(item, dict):
            continue
        if text_value(item.get("segment_id")) == segment["segment_id"] or int(item.get("index") or 0) == int(segment["index"]):
            found, values = bbox_values_from_detection(item)
            return (found, values) if found else (False, [])
    found, values = bbox_values_from_detection(detections)
    return (found, values) if found else (False, [])


def bbox_for_segment(detections: dict[str, Any], segment: dict[str, Any]) -> list[int] | None:
    found, values = raw_bbox_for_segment(detections, segment)
    if not found:
        return None
    if len(values) < 4:
        raise ToolBlocked("face_bbox_empty", f"Face bbox is empty for {segment['segment_id']}.")
    try:
        bbox = [int(float(v)) for v in values[:4]]
    except (TypeError, ValueError) as exc:
        raise ToolBlocked("face_bbox_invalid", f"Face bbox must contain numeric x/y/width/height for {segment['segment_id']}.") from exc
    if bbox[2] <= 0 or bbox[3] <= 0:
        raise ToolBlocked("face_bbox_empty", f"Face bbox width/height must be positive for {segment['segment_id']}.")
    return bbox


def post_mask_faces_for_segment(detections: dict[str, Any], segment: dict[str, Any]) -> list[Any]:
    for item in list_value(detections.get("segments")):
        if isinstance(item, dict) and (text_value(item.get("segment_id")) == segment["segment_id"] or int(item.get("index") or 0) == int(segment["index"])):
            return list_value(item.get("post_mask_faces"))
    return list_value(detections.get("post_mask_faces"))


def clamp_bbox(bbox: list[int], width: int, height: int) -> list[int]:
    x, y, w, h = bbox
    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    w = max(1, min(width - x, w))
    h = max(1, min(height - y, h))
    return [x, y, w, h]


def validate_bbox_in_frame(bbox: list[int] | None, width: int, height: int, segment_id: str) -> list[int] | None:
    if bbox is None:
        return None
    x, y, w, h = bbox
    if x < 0 or y < 0 or x + w > width or y + h > height:
        raise ToolBlocked(
            "face_bbox_out_of_bounds",
            f"Face bbox is outside the segment frame for {segment_id}.",
            {"bbox": bbox, "frame_width": width, "frame_height": height},
        )
    return bbox


def expand_bbox(bbox: list[int], width: int, height: int, config: dict[str, Any]) -> list[int]:
    x, y, w, h = clamp_bbox(bbox, width, height)
    ratios = {"left": 0.35, "right": 0.35, "top": 0.60, "bottom": 0.35, **dict_value(config.get("mask_expand_ratio"))}
    min_w = int(config.get("mask_min_width_pixels") or 32)
    min_h = int(config.get("mask_min_height_pixels") or 32)
    left = int(round(w * float(ratios["left"])))
    right = int(round(w * float(ratios["right"])))
    top = int(round(h * float(ratios["top"])))
    bottom = int(round(h * float(ratios["bottom"])))
    nx = max(0, x - left)
    ny = max(0, y - top)
    nw = min(width - nx, max(min_w, w + left + right))
    nh = min(height - ny, max(min_h, h + top + bottom))
    return [nx, ny, nw, nh]


def render_grid_black(frame: Any, bbox: list[int], config: dict[str, Any], cv2: Any) -> Any:
    x, y, w, h = bbox
    output = frame.copy()
    cv2.rectangle(output, (x, y), (x + w, y + h), (0, 0, 0), thickness=-1)
    line_width = int(config.get("grid_line_width") or 2)
    cell = max(4, int(config.get("grid_cell_size") or 18))
    color = (43, 43, 43)
    for px in range(x, x + w + 1, cell):
        cv2.line(output, (px, y), (px, y + h), color, line_width)
    for py in range(y, y + h + 1, cell):
        cv2.line(output, (x, py), (x + w, py), color, line_width)
    cv2.rectangle(output, (x, y), (x + w, y + h), color, line_width)
    return output


def privacy_grid_visual(config: dict[str, Any], width: int, height: int) -> tuple[float, int, tuple[int, int, int]]:
    grid = dict_value(config.get("privacy_grid"))
    short_edge = max(1, min(width, height))
    requested_line_width = float_value(grid.get("line_width_reference"), 1.0)
    line_width = 0.5 if requested_line_width < 0.75 else 1.0
    configured_cell = float_value(grid.get("cell_size_reference"), PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE)
    cell_reference = min(max(configured_cell, PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE), PRIVACY_GRID_MAX_CELL_SIZE_REFERENCE)
    cell = max(int(round(cell_reference)), int(round(cell_reference * short_edge / 1080.0)))
    return line_width, cell, (31, 31, 255)


def privacy_grid_raster_line_width(line_width: float) -> int:
    return max(1, int(round(line_width)))


def privacy_grid_line_opacity(line_width: float) -> float:
    return 0.5 if line_width <= 0.5 else 1.0


def privacy_grid_red_pixels(pixels: Any, line_width: float, np: Any) -> Any:
    if line_width <= 0.5:
        red_delta = pixels[:, 2].astype(np.int16) - np.minimum(
            pixels[:, 0].astype(np.int16), pixels[:, 1].astype(np.int16)
        )
        return (pixels[:, 2] >= 100) & (red_delta >= 40)
    return (pixels[:, 2] >= 180) & (pixels[:, 2] >= pixels[:, 1] * 1.5) & (pixels[:, 2] >= pixels[:, 0] * 1.5)


def render_privacy_grid(frame: Any, bbox: list[int], config: dict[str, Any], cv2: Any) -> Any:
    x, y, w, h = bbox
    output = frame.copy()
    line_width, cell, color = privacy_grid_visual(config, frame.shape[1], frame.shape[0])
    raster_width = privacy_grid_raster_line_width(line_width)
    opacity = privacy_grid_line_opacity(line_width)
    target = output if opacity >= 1.0 else output.copy()
    for px in range(x, x + w + 1, cell):
        cv2.line(target, (px, y), (px, y + h), color, raster_width)
    for py in range(y, y + h + 1, cell):
        cv2.line(target, (x, py), (x + w, py), color, raster_width)
    cv2.rectangle(target, (x, y), (x + w, y + h), color, raster_width)
    if opacity < 1.0:
        cv2.addWeighted(target, opacity, output, 1.0 - opacity, 0, output)
    return output


def privacy_grid_line_presence(image: Any, bbox: list[int], config: dict[str, Any], np: Any) -> float:
    x, y, w, h = bbox
    line_width, cell, _color = privacy_grid_visual(config, image.shape[1], image.shape[0])
    mask = np.zeros(image.shape[:2], dtype=bool)
    radius = max(0, privacy_grid_raster_line_width(line_width) // 2)
    for px in range(x, x + w + 1, cell):
        mask[max(0, y):min(image.shape[0], y + h + 1), max(0, px - radius):min(image.shape[1], px + radius + 1)] = True
    for py in range(y, y + h + 1, cell):
        mask[max(0, py - radius):min(image.shape[0], py + radius + 1), max(0, x):min(image.shape[1], x + w + 1)] = True
    pixels = image[mask]
    if pixels.size == 0:
        return 0.0
    red = privacy_grid_red_pixels(pixels, line_width, np)
    return float(np.mean(red))


def qa_metrics(original: Any, masked: Any, bbox: list[int], np: Any) -> dict[str, float]:
    x, y, w, h = bbox
    original_region = original[y:y + h, x:x + w]
    masked_region = masked[y:y + h, x:x + w]
    if original_region.size == 0 or masked_region.size == 0:
        return {"grid_black_black_pixel_ratio": 0.0, "masked_region_diff_mean": 0.0}
    black = np.all(masked_region < 18, axis=2)
    diff = np.abs(masked_region.astype("int16") - original_region.astype("int16"))
    return {
        "grid_black_black_pixel_ratio": float(np.mean(black)),
        "masked_region_diff_mean": float(np.mean(diff)),
    }


def reference_privacy_mode(config: dict[str, Any]) -> str:
    mode = text_value(config.get("reference_privacy_mode") or "provider_safe_pose").lower()
    aliases = {
        "": "provider_safe_pose",
        "default": "provider_safe_pose",
        "provider_safe": "provider_safe_pose",
        "pose": "provider_safe_pose",
        "skeleton": "provider_safe_pose",
        "outline": "provider_safe_outline",
        "edge": "provider_safe_outline",
        "edge_outline": "provider_safe_outline",
        "face_only": "face_mask_only",
        "face_mask": "face_mask_only",
        "face_masked": "face_mask_only",
        "none": "face_mask_only",
    }
    mode = aliases.get(mode, mode)
    if mode not in {"provider_safe_pose", "provider_safe_outline", "face_mask_only", "red_grid_guide"}:
        raise ToolBlocked("reference_privacy_mode_invalid", f"Unsupported reference_privacy_mode: {mode}")
    return mode


def create_reference_privacy_context(config: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    mode = reference_privacy_mode(config)
    context: dict[str, Any] = {
        "mode": mode,
        "requested_mode": mode,
        "pose_frames": 0,
        "outline_fallback_frames": 0,
    }
    if mode != "provider_safe_pose":
        return context
    try:
        import mediapipe as mp  # type: ignore
        model_complexity = int_value(config.get("provider_safe_pose_model_complexity"), 1)
        context["mp_pose"] = mp.solutions.pose
        context["pose"] = mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=max(0, min(2, model_complexity)),
            enable_segmentation=False,
            min_detection_confidence=float_value(config.get("provider_safe_pose_min_detection_confidence"), 0.35),
            min_tracking_confidence=float_value(config.get("provider_safe_pose_min_tracking_confidence"), 0.35),
        )
    except Exception as exc:
        context["mode"] = "provider_safe_outline"
        warnings.append({
            "code": "provider_safe_pose_unavailable",
            "message": f"MediaPipe Pose unavailable; using outline privacy mode instead: {exc}",
        })
    return context


def close_reference_privacy_context(context: dict[str, Any]) -> None:
    pose = context.get("pose")
    if pose is not None and hasattr(pose, "close"):
        try:
            pose.close()
        except Exception:
            pass


def render_provider_safe_outline(frame: Any, cv2: Any, np: Any) -> Any:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 45, 125)
    edges = cv2.dilate(edges, np.ones((2, 2), dtype=np.uint8), iterations=1)
    output = np.zeros_like(frame)
    output[:, :] = (14, 15, 16)
    output[edges > 0] = (232, 232, 226)
    return output


def render_provider_safe_pose(frame: Any, context: dict[str, Any], cv2: Any, np: Any) -> Any:
    pose = context.get("pose")
    mp_pose = context.get("mp_pose")
    if pose is None or mp_pose is None:
        context["outline_fallback_frames"] = int_value(context.get("outline_fallback_frames"), 0) + 1
        return render_provider_safe_outline(frame, cv2, np)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    try:
        result = pose.process(rgb)
    except Exception:
        context["outline_fallback_frames"] = int_value(context.get("outline_fallback_frames"), 0) + 1
        return render_provider_safe_outline(frame, cv2, np)
    height, width = frame.shape[:2]
    output = np.zeros_like(frame)
    output[:, :] = (12, 14, 16)
    landmarks = getattr(result, "pose_landmarks", None)
    if not landmarks:
        context["outline_fallback_frames"] = int_value(context.get("outline_fallback_frames"), 0) + 1
        return render_provider_safe_outline(frame, cv2, np)
    points: dict[int, tuple[int, int]] = {}
    for index, landmark in enumerate(landmarks.landmark):
        visibility = float(getattr(landmark, "visibility", 1.0) or 0.0)
        presence = float(getattr(landmark, "presence", 1.0) or 0.0)
        if visibility < 0.18 or presence < 0.18:
            continue
        x = int(round(float(landmark.x) * width))
        y = int(round(float(landmark.y) * height))
        if 0 <= x < width and 0 <= y < height:
            points[index] = (x, y)
    for left, right in mp_pose.POSE_CONNECTIONS:
        if left in points and right in points:
            cv2.line(output, points[left], points[right], (230, 238, 233), 5, lineType=cv2.LINE_AA)
            cv2.line(output, points[left], points[right], (54, 149, 245), 2, lineType=cv2.LINE_AA)
    for point in points.values():
        cv2.circle(output, point, 5, (245, 246, 240), thickness=-1, lineType=cv2.LINE_AA)
        cv2.circle(output, point, 2, (35, 114, 210), thickness=-1, lineType=cv2.LINE_AA)
    context["pose_frames"] = int_value(context.get("pose_frames"), 0) + 1
    return output


def render_reference_privacy_frame(frame: Any, context: dict[str, Any], cv2: Any, np: Any) -> Any:
    mode = text_value(context.get("mode") or "provider_safe_pose")
    if mode == "face_mask_only":
        return frame
    if mode == "provider_safe_outline":
        context["outline_fallback_frames"] = int_value(context.get("outline_fallback_frames"), 0) + 1
        return render_provider_safe_outline(frame, cv2, np)
    return render_provider_safe_pose(frame, context, cv2, np)


def reencode_reference_video_for_provider(
    video_path: Path,
    config: dict[str, Any],
    *,
    width: int,
    height: int,
    privacy_mode: str,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    max_bytes = positive_int(config.get("provider_reference_max_bytes"), 49_000_000)
    before_bytes = video_path.stat().st_size if video_path.exists() else 0

    base_crf = positive_int(config.get("provider_reference_video_crf"), 28)
    preset = text_value(config.get("provider_reference_video_preset") or "veryfast")
    max_height = positive_int(config.get("provider_reference_video_max_height"), 1280)
    attempts: list[dict[str, Any]] = [{"crf": base_crf, "width": width, "height": height, "scaled": False}]
    if height > max_height and max_height >= 240:
        scaled_width = max(2, int(round(width * (max_height / float(height)))))
        if scaled_width % 2:
            scaled_width += 1
        attempts.append({"crf": max(base_crf, 32), "width": scaled_width, "height": max_height, "scaled": True})

    last_error = ""
    for index, attempt in enumerate(attempts, start=1):
        tmp = video_path.with_name(f"{video_path.stem}.provider_h264_attempt_{index}.mp4")
        if tmp.exists():
            remove_path(tmp)
        command = [
            find_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(attempt["crf"]),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
        ]
        if attempt["scaled"]:
            command.extend(["-vf", f"scale={attempt['width']}:{attempt['height']}"])
        command.append(str(tmp))
        completed = run_command(command, timeout=600)
        if completed.returncode != 0 or not tmp.exists() or tmp.stat().st_size <= 0:
            last_error = (completed.stderr or completed.stdout or "ffmpeg h264 reencode failed")[:2000]
            remove_path(tmp)
            continue
        after_bytes = tmp.stat().st_size
        if after_bytes <= max_bytes:
            shutil.move(str(tmp), str(video_path))
            warnings.append({
                "code": "provider_reference_video_reencoded",
                "message": "Reference video was finalized as H.264 MP4 for web playback and provider upload compatibility.",
                "before_bytes": before_bytes,
                "after_bytes": after_bytes,
                "max_bytes": max_bytes,
                "crf": attempt["crf"],
                "scaled": bool(attempt["scaled"]),
            })
            return {
                "compressed": True,
                "size_bytes": after_bytes,
                "before_bytes": before_bytes,
                "max_bytes": max_bytes,
                "crf": attempt["crf"],
                "scaled": bool(attempt["scaled"]),
            }
        last_error = f"Re-encoded file is still larger than provider limit: {after_bytes} > {max_bytes}"
        remove_path(tmp)

    raise ToolBlocked(
        "face_masked_reference_video_h264_finalize_failed",
        f"Face-masked reference video could not be finalized as H.264 MP4: {last_error}",
        {"path": str(video_path), "size_bytes": before_bytes, "max_bytes": max_bytes, "last_error": last_error},
    )


def write_segment_videos(
    workspace: Path,
    source_video: Path,
    segment: dict[str, Any],
    bbox: list[int] | None,
    config: dict[str, Any],
    detections: dict[str, Any],
    result: dict[str, Any],
    privacy_grid_bbox: list[int] | None = None,
) -> dict[str, Any]:
    cv2, np = import_cv2_np()
    cap = cv2.VideoCapture(str(source_video))
    if not cap.isOpened():
        raise ToolFailed("video_open_failed", f"OpenCV cannot open {source_video}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        raise ToolFailed("video_open_failed", "OpenCV returned invalid video dimensions.")
    mode = reference_privacy_mode(config)
    bbox = validate_bbox_in_frame(privacy_grid_bbox if mode == "red_grid_guide" else bbox, width, height, segment["segment_id"])
    track_samples = [] if mode == "red_grid_guide" else sample_track_for_segment(detections, segment, width, height)
    segment_dir_rel = f"{SEGMENTS_DIR_REL}/Segment_{segment['index']:04d}"
    silent_rel = f"{segment_dir_rel}/Segment_{segment['index']:04d}_Reference_Silent.mp4"
    masked_rel = f"{segment_dir_rel}/Segment_{segment['index']:04d}_Reference_{'PrivacyGrid' if mode == 'red_grid_guide' else 'FaceMasked'}.mp4"
    track_rel = f"{segment_dir_rel}/Segment_{segment['index']:04d}_FaceTrack.json"
    qa_rel = f"{TOOL_META['02']['tool_dir']}/Report/qa_samples/Segment_{segment['index']:04d}_QA.jpg"
    for rel_path in (silent_rel, masked_rel):
        (workspace / rel_path).parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    silent_writer = cv2.VideoWriter(str(workspace / silent_rel), fourcc, fps, (width, height))
    masked_writer = cv2.VideoWriter(str(workspace / masked_rel), fourcc, fps, (width, height))
    if not silent_writer.isOpened() or not masked_writer.isOpened():
        raise ToolFailed("face_mask_write_failed", "OpenCV cannot open segment video writers.")
    summary_expanded = bbox if mode == "red_grid_guide" else (expand_bbox(bbox, width, height, config) if bbox else None)
    warnings: list[dict[str, Any]] = []
    privacy_context = create_reference_privacy_context(config, warnings)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(segment["start_frame"]))
    metrics: list[dict[str, float]] = []
    frames: list[dict[str, Any]] = []
    sample_frames = []
    for absolute_frame in range(int(segment["start_frame"]), int(segment["end_frame"]) + 1):
        ok, frame = cap.read()
        if not ok:
            break
        if mode == "red_grid_guide":
            frame_bbox, frame_confidence, frame_engine = bbox, 1.0, text_value(detections.get("face_detection_engine"))
            frame_expanded = bbox
            masked = render_privacy_grid(frame, bbox, config, cv2) if bbox else frame.copy()
        else:
            frame_bbox, frame_confidence, frame_engine = interpolated_track_bbox(track_samples, bbox, absolute_frame)
            frame_bbox = clamp_bbox(frame_bbox, width, height) if frame_bbox else None
            frame_expanded = expand_bbox(frame_bbox, width, height, config) if frame_bbox else None
            face_masked = render_grid_black(frame, frame_expanded, config, cv2) if frame_expanded else frame.copy()
            masked = render_reference_privacy_frame(face_masked, privacy_context, cv2, np)
            if frame_expanded and text_value(privacy_context.get("mode")) != "face_mask_only":
                masked = render_grid_black(masked, frame_expanded, config, cv2)
        silent_writer.write(frame)
        masked_writer.write(masked)
        face_entry = {
            "bbox": frame_bbox,
            "expanded_bbox": frame_expanded,
            "confidence": round(frame_confidence, 4) if frame_confidence else (1.0 if bbox else 0.0),
            "engine": frame_engine or text_value(detections.get("face_detection_engine")),
            "masked": bool(frame_expanded),
        } if frame_bbox and frame_expanded else None
        frames.append({
            "frame_index": absolute_frame,
            "segment_frame_index": absolute_frame - int(segment["start_frame"]),
            "faces": [face_entry] if face_entry else [],
        })
        if frame_expanded and mode != "red_grid_guide":
            metrics.append(qa_metrics(frame, masked, frame_expanded, np))
        elif frame_expanded:
            metrics.append({"privacy_grid_line_presence_ratio": privacy_grid_line_presence(masked, frame_expanded, config, np)})
        if len(sample_frames) < 3 and absolute_frame in {int(segment["start_frame"]), int((segment["start_frame"] + segment["end_frame"]) / 2), int(segment["end_frame"])}:
            display = masked.copy()
            if frame_expanded:
                x, y, w, h = frame_expanded
                cv2.rectangle(display, (x, y), (x + w, y + h), (0, 0, 255), 1)
            sample_frames.append(display)
    cap.release()
    close_reference_privacy_context(privacy_context)
    silent_writer.release()
    masked_writer.release()
    if not frames:
        raise ToolFailed("face_mask_write_failed", f"No frames written for {segment['segment_id']}.")
    if sample_frames:
        sheet = np.concatenate([cv2.resize(frame, (width, height)) for frame in sample_frames], axis=1)
        (workspace / qa_rel).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(workspace / qa_rel), sheet)
        result["created_files"].append(qa_rel)
    for rel_path in (silent_rel, masked_rel):
        path = workspace / rel_path
        if not path.exists() or path.stat().st_size <= 0:
            raise ToolBlocked("face_masked_video_empty", rel_path)
        result["created_files"].append(rel_path)
    provider_video = reencode_reference_video_for_provider(
        workspace / masked_rel,
        config,
        width=width,
        height=height,
        privacy_mode=text_value(privacy_context.get("mode")),
        warnings=warnings,
    )
    min_black = min((item.get("grid_black_black_pixel_ratio", 1.0) for item in metrics), default=0.0)
    min_diff = min((item.get("masked_region_diff_mean", 100.0) for item in metrics), default=0.0)
    min_grid_presence = min((item.get("privacy_grid_line_presence_ratio", 1.0) for item in metrics), default=0.0)
    qa_status = "passed" if bbox else "skipped_no_face"
    if not bbox:
        warnings.append({"code": "face_not_detected", "message": f"No face bbox was provided for {segment['segment_id']}."})
    if mode == "red_grid_guide" and bbox and min_grid_presence < 0.95:
        raise ToolBlocked("privacy_grid_line_qa_failed", f"Privacy grid QA failed for {segment['segment_id']}: line_presence={min_grid_presence:.3f}")
    if mode != "red_grid_guide" and bbox and (min_black < float(config.get("grid_black_black_pixel_ratio_min") or 0.60) or min_diff < float(config.get("masked_region_diff_mean_min") or 15.0)):
        raise ToolBlocked("face_mask_qa_failed", f"Mask QA failed for {segment['segment_id']}: black_ratio={min_black:.3f}, diff_mean={min_diff:.3f}")
    if post_mask_faces_for_segment(detections, segment):
        warnings.append({"code": "post_mask_face_detected", "message": "Fake post-mask detector still reported a face."})
        qa_status = "warning"
    track = {
        "schema_version": "dance_mimic_v1_face_track_0.1",
        "segment_id": segment["segment_id"],
        "detection_engine": text_value(detections.get("face_detection_engine")),
        "requested_detection_engine": text_value(detections.get("requested_face_detection_engine")),
        "frame_count": len(frames),
        "mask_config": config,
        "detection_samples": track_samples,
        "frames": frames,
        "summary": {
            "detected_face_tracks": 1 if bbox else 0,
            "frames_with_faces": sum(1 for frame in frames if list_value(frame.get("faces"))),
            "frames_without_faces": sum(1 for frame in frames if not list_value(frame.get("faces"))),
            "masked_frames": sum(1 for frame in frames for face in list_value(frame.get("faces")) if dict_value(face).get("masked")),
            "reference_privacy_mode": text_value(privacy_context.get("mode")),
            "requested_reference_privacy_mode": text_value(privacy_context.get("requested_mode")),
            "provider_safe_pose_frames": int_value(privacy_context.get("pose_frames"), 0),
            "provider_safe_outline_fallback_frames": int_value(privacy_context.get("outline_fallback_frames"), 0),
        },
    }
    write_json(workspace / track_rel, track)
    result["created_files"].append(track_rel)
    try:
        output_probe = probe_media(workspace / masked_rel)
    except ToolBlocked as exc:
        raise ToolBlocked("face_masked_video_probe_failed", f"Could not probe masked reference video for {segment['segment_id']}: {exc.message}") from exc
    if int(output_probe.get("frame_count") or 0) <= 0 or int(output_probe.get("width") or 0) <= 0 or int(output_probe.get("height") or 0) <= 0:
        raise ToolBlocked("face_masked_video_empty", f"Masked reference video probe is empty for {segment['segment_id']}: {masked_rel}")
    mask_area_ratio = 0.0
    if summary_expanded and width > 0 and height > 0:
        mask_area_ratio = (float(summary_expanded[2]) * float(summary_expanded[3])) / float(width * height)
    return {
        **segment,
        "silent_video_path": silent_rel,
        "face_track_path": track_rel,
        "face_masked_reference_video_path": masked_rel,
        "provider_reference_video_path": masked_rel,
        "reference_video_grid_applied": mode == "red_grid_guide",
        "face_summary": track["summary"],
        "qa": {
            "status": qa_status,
            "face_detected": bool(bbox),
            "bbox": bbox,
            "expanded_bbox": summary_expanded,
            "detection_engine": text_value(detections.get("face_detection_engine")),
            "requested_detection_engine": text_value(detections.get("requested_face_detection_engine")),
            "detection_sample_count": len(track_samples),
            "reference_privacy_mode": text_value(privacy_context.get("mode")),
            "requested_reference_privacy_mode": text_value(privacy_context.get("requested_mode")),
            "provider_safe_pose_frames": int_value(privacy_context.get("pose_frames"), 0),
            "provider_safe_outline_fallback_frames": int_value(privacy_context.get("outline_fallback_frames"), 0),
            "mask_coverage": {
                "masked_area_ratio": round(mask_area_ratio, 6),
                "grid_black_black_pixel_ratio_min": round(min_black, 4),
                "masked_region_diff_mean_min": round(min_diff, 3),
                "privacy_grid_line_presence_ratio_min": round(min_grid_presence, 4) if mode == "red_grid_guide" else None,
                "sampled_frame_count": len(metrics),
            },
            "output_probe": {
                "duration": output_probe.get("duration"),
                "fps": output_probe.get("fps"),
                "frame_count": output_probe.get("frame_count"),
                "width": output_probe.get("width"),
                "height": output_probe.get("height"),
                "has_audio": output_probe.get("has_audio"),
                "size_bytes": int((workspace / masked_rel).stat().st_size),
            },
            "provider_reference_video": provider_video,
            "sample_sheet_path": qa_rel if sample_frames else "",
            "grid_black_black_pixel_ratio": round(min_black, 4),
            "masked_region_diff_mean": round(min_diff, 3),
            "warnings": warnings,
        },
        "warnings": warnings,
    }


def build_target_identity_privacy_grid(workspace: Path, variables: dict[str, Any], config: dict[str, Any], enabled: bool, result: dict[str, Any]) -> dict[str, Any]:
    source_rel = text_value(variables.get("target_identity_image_path"))
    if not source_rel:
        raise ToolBlocked("missing_target_identity_image_path", f"{VARIABLES_REL}.target_identity_image_path is required.")
    source = workspace_path(workspace, source_rel)
    if not source.exists() or not source.is_file() or source.stat().st_size <= 0:
        raise ToolBlocked("target_identity_image_missing", f"Missing target identity image: {source_rel}")
    source_hash = file_fingerprint(source)["sha256"]
    if not enabled:
        return {"grid_applied": False, "skip_reason": "user_disabled", "source_path": source_rel, "source_sha256": source_hash, "provider_path": source_rel, "provider_sha256": source_hash, "face_count": None, "expanded_bbox": None}
    cv2, np = import_cv2_np()
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ToolBlocked("target_identity_image_unsupported", f"Could not decode target identity image: {source_rel}")
    faces, engine = detect_faces_in_image(image, config)
    if not faces:
        raise ToolBlocked("target_identity_face_not_detected", "Target identity image privacy grid requires exactly one detected face.")
    if len(faces) > 1:
        raise ToolBlocked("target_identity_multiple_faces", "Target identity image privacy grid requires exactly one detected face.", {"face_count": len(faces)})
    bbox = list_value(faces[0].get("bbox"))
    x, y, w, h = bbox
    height, width = image.shape[:2]
    expanded = clamp_bbox([int(round(x - 0.15 * w)), int(round(y - 0.25 * h)), int(round(w * 1.30)), int(round(h * 1.45))], width, height)
    rendered = render_privacy_grid(image, expanded, config, cv2)
    presence = privacy_grid_line_presence(rendered, expanded, config, np)
    if presence < 0.95:
        raise ToolBlocked("target_identity_privacy_grid_render_failed", f"Target identity privacy grid line QA failed: {presence:.3f}")
    provider_rel = "SessionContext/Target_Identity_Image_PrivacyGrid.png"
    provider = workspace / provider_rel
    if not cv2.imwrite(str(provider), rendered) or not provider.exists() or provider.stat().st_size <= 0:
        raise ToolBlocked("target_identity_privacy_grid_render_failed", f"Could not write {provider_rel}.")
    result["created_files"].append(provider_rel)
    return {
        "grid_applied": True,
        "skip_reason": None,
        "source_path": source_rel,
        "source_sha256": source_hash,
        "provider_path": provider_rel,
        "provider_sha256": file_fingerprint(provider)["sha256"],
        "face_count": 1,
        "face_bbox": bbox,
        "expanded_bbox": expanded,
        "detection_engine": engine,
        "line_presence_ratio": round(presence, 4),
    }


def representative_privacy_grid_sample(detections: dict[str, Any], duration_seconds: float) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for segment in list_value(detections.get("segments")):
        for sample in list_value(dict_value(segment).get("samples")):
            item = dict_value(sample)
            if list_value(item.get("faces")) or list_value(item.get("bbox")):
                candidates.append(item)
    if not candidates:
        raise ToolBlocked("privacy_grid_preview_sample_missing", "Reference video privacy grid preview requires a detected-face sample.")
    midpoint = max(0.0, duration_seconds) / 2.0
    return min(
        candidates,
        key=lambda item: (
            abs(float_value(item.get("timestamp_seconds"), 0.0) - midpoint),
            int_value(item.get("frame_index"), 0),
        ),
    )


def build_reference_privacy_grid_preview(
    workspace: Path,
    source_video: Path,
    detections: dict[str, Any],
    fixed_grid: dict[str, Any],
    config: dict[str, Any],
    duration_seconds: float,
    result: dict[str, Any],
) -> dict[str, Any]:
    cv2, np = import_cv2_np()
    sample = representative_privacy_grid_sample(detections, duration_seconds)
    frame_index = int_value(sample.get("frame_index"), 0)
    cap = cv2.VideoCapture(str(source_video))
    try:
        if not cap.isOpened():
            raise ToolFailed("privacy_grid_preview_video_open_failed", f"OpenCV cannot open {source_video}")
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None or frame.size == 0:
        raise ToolBlocked("privacy_grid_preview_frame_read_failed", f"Could not read reference preview frame {frame_index}.")
    bbox = list_value(fixed_grid.get("bbox"))
    if len(bbox) != 4:
        raise ToolBlocked("privacy_grid_preview_region_missing", "Reference privacy grid preview requires the fixed grid region.")
    rendered = render_privacy_grid(frame, bbox, config, cv2)
    presence = privacy_grid_line_presence(rendered, bbox, config, np)
    if presence < 0.95:
        raise ToolBlocked("privacy_grid_preview_render_failed", f"Reference privacy grid preview line QA failed: {presence:.3f}")
    output = workspace / PRIVACY_GRID_REFERENCE_PREVIEW_REL
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), rendered) or not output.exists() or output.stat().st_size <= 0:
        raise ToolBlocked("privacy_grid_preview_render_failed", f"Could not write {PRIVACY_GRID_REFERENCE_PREVIEW_REL}.")
    result["created_files"].append(PRIVACY_GRID_REFERENCE_PREVIEW_REL)
    fingerprint = file_fingerprint(output)
    return {
        "path": PRIVACY_GRID_REFERENCE_PREVIEW_REL,
        "sha256": fingerprint["sha256"],
        "size_bytes": fingerprint["size_bytes"],
        "frame_index": frame_index,
        "timestamp_seconds": round(float_value(sample.get("timestamp_seconds"), 0.0), 3),
        "line_presence_ratio": round(presence, 4),
    }


def run_02(workspace: Path, args: Args) -> dict[str, Any]:
    require_workflow(args)
    prepared = ensure_dirs(workspace, TOOL_META["02"]["tool_dir"], [SEGMENTS_DIR_REL, f"{TOOL_META['02']['tool_dir']}/Report/qa_samples"])
    result = base_result("02", workspace, args, prepared)
    variables = load_variables(workspace)
    media_manifest_path = workspace / REFERENCE_MANIFEST_REL
    if not media_manifest_path.exists():
        raise ToolBlocked("reference_media_manifest_missing", f"Missing {REFERENCE_MANIFEST_REL}.")
    media_manifest = read_json(media_manifest_path)
    silent = workspace / SILENT_VIDEO_REL
    if not silent.exists() or silent.stat().st_size <= 0:
        raise ToolBlocked("silent_reference_video_missing", f"Missing {SILENT_VIDEO_REL}.")
    probe = probe_media(silent)
    if probe["has_audio"]:
        raise ToolBlocked("silent_reference_video_has_audio", f"{SILENT_VIDEO_REL} must not contain audio.")
    config = {**dict_value(default_variables()["reference_face_masked_video_build"]), **dict_value(variables.get("reference_face_masked_video_build"))}
    config["privacy_grid"] = {**dict_value(dict_value(default_variables()["reference_face_masked_video_build"]).get("privacy_grid")), **dict_value(config.get("privacy_grid"))}
    config["privacy_grid"]["cell_size_reference"] = min(
        max(
            float_value(config["privacy_grid"].get("cell_size_reference"), PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE),
            PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE,
        ),
        PRIVACY_GRID_MAX_CELL_SIZE_REFERENCE,
    )
    split_config = {**dict_value(default_variables()["storyboard_split_config"]), **dict_value(variables.get("storyboard_split_config"))}
    target = float(args.target_video_seconds or split_config.get("target_video_seconds") or 8.0)
    minimum = float(args.minimum_video_seconds or split_config.get("minimum_video_seconds") or 4.0)
    if args.block_on_face_not_detected:
        config["block_on_face_not_detected"] = True
    segments = split_segments(float(probe["duration"]), float(probe["fps"] or 24.0), int(probe["frame_count"]), target, minimum)
    mode = reference_privacy_mode(config)
    grid_config = dict_value(config.get("privacy_grid"))
    apply_reference_grid = bool(grid_config.get("apply_to_reference_video", True)) if mode == "red_grid_guide" else False
    apply_target_grid = bool(grid_config.get("apply_to_target_identity_image", True)) if mode == "red_grid_guide" else False
    if mode == "red_grid_guide":
        remove_path(workspace / PRIVACY_GRID_MANIFEST_REL)
        remove_path(workspace / PRIVACY_GRID_REFERENCE_PREVIEW_REL)
        variables.pop("privacy_grid_manifest_path", None)
        variables.pop("provider_target_identity_image_path", None)
        write_json(workspace / VARIABLES_REL, variables)
    target_grid = build_target_identity_privacy_grid(workspace, variables, config, apply_target_grid, result) if mode == "red_grid_guide" else {}
    if mode == "red_grid_guide" and not apply_reference_grid:
        detections = {"schema_version": "dance_mimic_v1_face_detections_skipped_0.1", "requested_face_detection_engine": "", "face_detection_engine": "", "segments": [], "warnings": [], "source": "user_disabled"}
    else:
        detections = resolve_detection_manifest(workspace, args, variables, silent, segments, config, probe, result)
    fixed_grid = privacy_grid_region(detections, int(probe["width"]), int(probe["height"]), config) if apply_reference_grid else None
    reference_grid_preview = build_reference_privacy_grid_preview(
        workspace,
        silent,
        detections,
        dict_value(fixed_grid),
        config,
        float(probe["duration"]),
        result,
    ) if apply_reference_grid else {}
    if args.force:
        remove_path(workspace / SEGMENTS_DIR_REL)
        (workspace / SEGMENTS_DIR_REL).mkdir(parents=True, exist_ok=True)
    completed_segments = []
    for segment in segments:
        bbox = bbox_for_segment(detections, segment) if mode != "red_grid_guide" else None
        if mode != "red_grid_guide" and not bbox and bool(config.get("block_on_face_not_detected")):
            raise ToolBlocked("face_not_detected", f"No face bbox for {segment['segment_id']}.")
        render_config = config if apply_reference_grid or mode != "red_grid_guide" else {**config, "reference_privacy_mode": "face_mask_only"}
        completed_segments.append(write_segment_videos(workspace, silent, segment, bbox, render_config, detections, result, privacy_grid_bbox=list_value(dict_value(fixed_grid).get("bbox")) or None))
        completed_segments[-1]["reference_video_grid_applied"] = apply_reference_grid
    manifest = {
        "schema_version": "dance_mimic_v1_reference_segments_0.2",
        "tool": TOOL_META["02"]["tool_id"],
        "workflow_id": WORKFLOW_ID,
        "source_video": SILENT_VIDEO_REL,
        "source_fingerprint": file_fingerprint(silent),
        "source_video_probe": probe,
        "reference_media_manifest_path": REFERENCE_MANIFEST_REL,
        "split_config": {"target_video_seconds": target, "minimum_video_seconds": minimum, "split_algorithm": "frame_accurate_near_even_tail_guard"},
        "face_mask_config": config,
        "face_detection": {
            "requested_face_detection_engine": text_value(detections.get("requested_face_detection_engine")),
            "face_detection_engine": text_value(detections.get("face_detection_engine")),
            "schema_version": text_value(detections.get("schema_version")),
            "source": text_value(detections.get("source")),
            "warnings": list_value(detections.get("warnings")),
        },
        "segments": completed_segments,
        "warnings": [*list_value(detections.get("warnings")), *[warning for segment in completed_segments for warning in list_value(segment.get("warnings"))]],
        "created_at": now_iso(),
    }
    tool_manifest_rel = f"{TOOL_META['02']['tool_dir']}/Output/reference_segments_manifest.json"
    write_json(workspace / tool_manifest_rel, manifest)
    write_json(workspace / SEGMENTS_MANIFEST_REL, manifest)
    result["created_files"].extend([tool_manifest_rel, SEGMENTS_MANIFEST_REL])
    if mode == "red_grid_guide":
        reference_grid = {
            "grid_applied": apply_reference_grid,
            "skip_reason": None if apply_reference_grid else "user_disabled",
            "source_path": SOURCE_VIDEO_REL,
            "source_sha256": file_fingerprint(workspace / SOURCE_VIDEO_REL)["sha256"],
            "normalized_region": dict_value(fixed_grid).get("normalized_region") if fixed_grid else None,
            "valid_face_sample_count": dict_value(fixed_grid).get("valid_face_sample_count") if fixed_grid else None,
            "face_sample_coverage_ratio": dict_value(fixed_grid).get("face_sample_coverage_ratio") if fixed_grid else None,
            "face_area_coverage_ratio": dict_value(fixed_grid).get("face_area_coverage_ratio") if fixed_grid else None,
            "region_area_ratio": dict_value(fixed_grid).get("region_area_ratio") if fixed_grid else None,
            "region_source": dict_value(fixed_grid).get("region_source") if fixed_grid else None,
            "fixed_across_segments": bool(apply_reference_grid),
            "preview": reference_grid_preview if apply_reference_grid else None,
            "provider_segments": [
                {"segment_id": text_value(item.get("segment_id")), "provider_path": text_value(item.get("provider_reference_video_path")), "provider_sha256": file_fingerprint(workspace_path(workspace, text_value(item.get("provider_reference_video_path"))))["sha256"]}
                for item in completed_segments
            ],
        }
        scope = "both" if apply_reference_grid and apply_target_grid else ("reference_video" if apply_reference_grid else ("target_identity" if apply_target_grid else "none"))
        privacy_manifest = {
            "schema_version": "dance_mimic_v1_privacy_grid_0.2",
            "mode": "red_grid_guide",
            "apply_to_reference_video": apply_reference_grid,
            "apply_to_target_identity_image": apply_target_grid,
            "effective_grid_scope": scope,
            "identity_visible": True,
            "privacy_strength": "low",
            "reference_video": reference_grid,
            "target_identity": target_grid,
            "render": {"line_color": "#ff1f1f", "line_width_reference": float_value(grid_config.get("line_width_reference"), 1.0), "cell_size_reference": int_value(grid_config.get("cell_size_reference"), PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE), "fill_alpha": 0},
            "created_at": now_iso(),
        }
        write_json(workspace / PRIVACY_GRID_MANIFEST_REL, privacy_manifest)
        result["created_files"].append(PRIVACY_GRID_MANIFEST_REL)
        variables["privacy_grid_manifest_path"] = PRIVACY_GRID_MANIFEST_REL
        variables["provider_target_identity_image_path"] = text_value(target_grid.get("provider_path"))
        write_json(workspace / VARIABLES_REL, variables)
        result["outputs"]["privacy_grid_manifest"] = PRIVACY_GRID_MANIFEST_REL
    result["warnings"].extend(manifest["warnings"])
    result["inputs"] = {"variables": VARIABLES_REL, "silent_video": SILENT_VIDEO_REL, "reference_media_manifest": REFERENCE_MANIFEST_REL}
    result["outputs"] = {"manifest": tool_manifest_rel, "session_manifest": SEGMENTS_MANIFEST_REL, **({"privacy_grid_manifest": PRIVACY_GRID_MANIFEST_REL} if mode == "red_grid_guide" else {})}
    result["segment_count"] = len(completed_segments)
    result["face_mask_summary"] = {
        "segments_completed": len(completed_segments),
        "segments_with_faces": sum(1 for item in completed_segments if dict_value(item.get("face_summary")).get("frames_with_faces")),
        "segments_without_faces": sum(1 for item in completed_segments if not dict_value(item.get("face_summary")).get("frames_with_faces")),
        "total_detected_face_tracks": sum(int(dict_value(item.get("face_summary")).get("detected_face_tracks") or 0) for item in completed_segments),
        "total_masked_frames": sum(int(dict_value(item.get("face_summary")).get("masked_frames") or 0) for item in completed_segments),
        "requested_face_detection_engine": text_value(detections.get("requested_face_detection_engine")),
        "face_detection_engine": text_value(detections.get("face_detection_engine")),
    }
    clear_stale_items(workspace, result, ["02_reference_face_masked_video_build"])
    if args.force:
        mark_downstream_stale(
            workspace,
            result,
            source_step="02_ReferenceFaceMaskedVideoBuild",
            reason="reference_face_mask_force_rerun",
            items={
                "03_storyboard_standard_task_build": [STORYBOARD_REL, STORYBOARD_SEED_REL],
                "storyboard_reference_video_assets": [f"{STORYBOARD_VIDEO_ASSETS_REL}/*_Reference_FaceMasked.mp4"],
                "video_generation_plan": [VIDEO_PLAN_REL, VIDEO_PLAN_EXECUTION_RESULT_REL, VIDEO_PLAN_EXECUTION_STATE_REL],
                "video_only_generation_plan": [VIDEO_ONLY_PLAN_REL, VIDEO_ONLY_PLAN_EXECUTION_RESULT_REL, VIDEO_ONLY_PLAN_EXECUTION_STATE_REL],
            },
        )
    return result


def segment_time(segment: dict[str, Any], key: str) -> float:
    candidates = {
        "start": ("start_seconds", "start"),
        "end": ("end_seconds", "end"),
        "duration": ("duration_seconds", "duration"),
    }[key]
    for candidate in candidates:
        try:
            return float(segment.get(candidate))
        except (TypeError, ValueError):
            pass
    return 0.0


def float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate_reference_segment_qa(segment: dict[str, Any], face_config: dict[str, Any]) -> None:
    segment_id = text_value(segment.get("segment_id")) or text_value(segment.get("dialogue_asset_key")) or "unknown_segment"
    qa = dict_value(segment.get("qa"))
    if not qa:
        raise ToolBlocked("reference_segment_qa_missing", f"Missing face-mask QA summary for {segment_id}.")
    status = text_value(qa.get("status"))
    if status not in {"passed", "warning"}:
        raise ToolBlocked("reference_segment_qa_failed", f"Face-mask QA did not pass for {segment_id}: {status or 'missing_status'}.")
    if not bool(qa.get("face_detected")):
        raise ToolBlocked("reference_segment_qa_failed", f"Face-mask QA did not detect a face for {segment_id}.")
    bbox = list_value(qa.get("bbox"))
    if len(bbox) < 4:
        raise ToolBlocked("reference_segment_qa_failed", f"Face-mask QA bbox is missing for {segment_id}.")
    coverage = dict_value(qa.get("mask_coverage"))
    if not coverage:
        raise ToolBlocked("reference_segment_qa_failed", f"Face-mask QA coverage summary is missing for {segment_id}.")
    black_min = float_value(coverage.get("grid_black_black_pixel_ratio_min"), float_value(qa.get("grid_black_black_pixel_ratio")))
    diff_min = float_value(coverage.get("masked_region_diff_mean_min"), float_value(qa.get("masked_region_diff_mean")))
    black_threshold = float_value(face_config.get("grid_black_black_pixel_ratio_min"), 0.60)
    diff_threshold = float_value(face_config.get("masked_region_diff_mean_min"), 15.0)
    if black_min < black_threshold or diff_min < diff_threshold:
        raise ToolBlocked(
            "reference_segment_qa_failed",
            f"Face-mask QA metrics are below threshold for {segment_id}: black_ratio={black_min:.3f}, diff_mean={diff_min:.3f}.",
        )
    output_probe = dict_value(qa.get("output_probe"))
    if int(output_probe.get("frame_count") or 0) <= 0 or int(output_probe.get("width") or 0) <= 0 or int(output_probe.get("height") or 0) <= 0:
        raise ToolBlocked("reference_segment_output_probe_missing", f"Face-masked output probe is missing or empty for {segment_id}.")
    size_bytes = int_value(output_probe.get("size_bytes"), 0)
    max_bytes = positive_int(face_config.get("provider_reference_max_bytes"), 49_000_000)
    if size_bytes > max_bytes:
        raise ToolBlocked(
            "face_masked_reference_video_too_large",
            f"Face-masked reference video is larger than provider limit for {segment_id}: {size_bytes} > {max_bytes}.",
        )


def dance_mimic_storyboard_existing_outputs(workspace: Path) -> list[str]:
    outputs: list[str] = []
    for rel_path in (STORYBOARD_REL, STORYBOARD_EDIT_REL, STORYBOARD_SEED_REL, STORYBOARD_WORKING_REL):
        path = workspace / rel_path
        if path.exists():
            outputs.append(rel_path)
    video_assets = workspace / STORYBOARD_VIDEO_ASSETS_REL
    if video_assets.exists():
        for path in sorted(video_assets.glob("*_Reference_FaceMasked.mp4")):
            if path.exists():
                outputs.append(rel(workspace, path))
    return outputs


def next_storyboard_archive_dir(workspace: Path) -> Path:
    base = workspace / STORYBOARD_ARCHIVE_DIR_REL / time.strftime("%Y%m%d_%H%M%S", time.localtime())
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = Path(f"{base}_{suffix:02d}")
        suffix += 1
    return candidate


def archive_dance_mimic_storyboard_outputs(workspace: Path, output_rels: list[str], created: list[str]) -> str:
    archive_dir = next_storyboard_archive_dir(workspace)
    moved: list[str] = []
    try:
        for rel_path in output_rels:
            source = workspace / rel_path
            if not source.exists():
                continue
            target = archive_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved.append(rel_path)
    except Exception as exc:
        raise ToolFailed("archive_failed", f"Failed to archive existing StoryBoard outputs: {exc}", {"archive_dir": rel(workspace, archive_dir), "moved": moved}) from exc
    archive_rel = rel(workspace, archive_dir)
    created.append(archive_rel)
    return archive_rel


def run_03(workspace: Path, args: Args) -> dict[str, Any]:
    require_workflow(args)
    prepared = ensure_dirs(workspace, TOOL_META["03"]["tool_dir"], ["SessionOutput/storyboard", STORYBOARD_VIDEO_ASSETS_REL, STORYBOARD_AUDIO_ASSETS_REL])
    result = base_result("03", workspace, args, prepared)
    variables = load_variables(workspace)
    if not text_value(variables.get("source_video_path")):
        raise ToolBlocked("missing_source_video_path", f"{VARIABLES_REL}.source_video_path is required.")
    target_identity_rel = text_value(variables.get("target_identity_image_path"))
    if not target_identity_rel:
        raise ToolBlocked("missing_target_identity_image_path", f"{VARIABLES_REL}.target_identity_image_path is required for DanceMimic final generation.")
    privacy_mode = reference_privacy_mode({**dict_value(default_variables()["reference_face_masked_video_build"]), **dict_value(variables.get("reference_face_masked_video_build"))})
    privacy_manifest = read_json(workspace / PRIVACY_GRID_MANIFEST_REL) if privacy_mode == "red_grid_guide" else {}
    if privacy_mode == "red_grid_guide" and not privacy_manifest:
        raise ToolBlocked("privacy_grid_manifest_missing", f"Missing {PRIVACY_GRID_MANIFEST_REL}.")
    provider_target_identity_rel = text_value(dict_value(privacy_manifest.get("target_identity")).get("provider_path") or variables.get("provider_target_identity_image_path") or target_identity_rel)
    target_identity_source = workspace_path(workspace, provider_target_identity_rel)
    if not target_identity_source.exists() or not target_identity_source.is_file():
        raise ToolBlocked("target_identity_image_missing", f"Missing target identity image: {target_identity_rel}")
    if target_identity_source.stat().st_size <= 0:
        raise ToolBlocked("target_identity_image_empty", f"Empty target identity image: {target_identity_rel}")
    target_identity_suffix = image_extension(target_identity_source)
    if not target_identity_suffix:
        raise ToolBlocked("target_identity_image_unsupported", f"Target identity image must be one of: {', '.join(sorted(IMAGE_EXTS))}")
    if not (workspace / REFERENCE_MANIFEST_REL).exists():
        raise ToolBlocked("missing_reference_media_manifest", f"Missing {REFERENCE_MANIFEST_REL}.")
    if not (workspace / SEGMENTS_MANIFEST_REL).exists():
        raise ToolBlocked("missing_reference_segments_manifest", f"Missing {SEGMENTS_MANIFEST_REL}.")
    media_manifest = read_json(workspace / REFERENCE_MANIFEST_REL)
    segments_manifest = read_json(workspace / SEGMENTS_MANIFEST_REL)
    face_config = {**dict_value(default_variables()["reference_face_masked_video_build"]), **dict_value(dict_value(segments_manifest).get("face_mask_config"))}
    created = result["created_files"]
    existing_outputs = dance_mimic_storyboard_existing_outputs(workspace)
    if existing_outputs and not args.force:
        raise ToolBlocked(
            "storyboard_existing_requires_force",
            "Existing StoryBoard outputs require --force before DanceMimic 03 can rebuild.",
            {"existing_outputs": existing_outputs},
        )
    if existing_outputs:
        archive_rel = archive_dance_mimic_storyboard_outputs(workspace, existing_outputs, created)
        result["warnings"].append({"code": "storyboard_outputs_archived", "message": f"Existing StoryBoard outputs archived to {archive_rel}.", "archive_dir": archive_rel})
    storyboard_segments = []
    seed_segments = []
    mixed_audio_rel = text_value(dict_value(media_manifest).get("outputs", {}).get("mixed_audio") or MIXED_AUDIO_REL)
    mixed_audio_path = workspace_path(workspace, mixed_audio_rel) if mixed_audio_rel else workspace / MIXED_AUDIO_REL
    mixed_audio_source = text_value(dict_value(media_manifest).get("audio_config", {}).get("mixed_audio_source"))
    has_reference_audio = bool(
        mixed_audio_rel
        and mixed_audio_path.exists()
        and mixed_audio_path.is_file()
        and mixed_audio_path.stat().st_size > 0
        and mixed_audio_source != "generated_silence"
    )
    if not has_reference_audio:
        result["warnings"].append({
            "code": "dance_mimic_reference_audio_unavailable",
            "message": "Reference video has no usable source audio; DanceMimic segment Audio slots will remain empty and downstream execution may use silence.",
            "mixed_audio_path": mixed_audio_rel,
            "mixed_audio_source": mixed_audio_source or "unknown",
        })
    for offset, segment in enumerate(list_value(dict_value(segments_manifest).get("segments")), start=1):
        if not isinstance(segment, dict):
            continue
        dak = text_value(segment.get("dialogue_asset_key")) or f"dak_{offset:04d}"
        source_rel = text_value(segment.get("provider_reference_video_path") or segment.get("face_masked_reference_video_path"))
        source = workspace_path(workspace, source_rel)
        if not source_rel or not source.exists():
            raise ToolBlocked("missing_face_masked_reference_video", f"Missing face-masked reference video for {dak}: {source_rel}")
        if source.stat().st_size <= 0:
            raise ToolBlocked("empty_face_masked_reference_video", f"Empty face-masked reference video for {dak}: {source_rel}")
        validate_reference_segment_qa(segment, face_config)
        target_rel = f"{STORYBOARD_VIDEO_ASSETS_REL}/{dak}_Reference_FaceMasked.mp4"
        copy_file(workspace, source, target_rel, created)
        target_source_image_rel = f"{STORYBOARD_WORKING_REL}/{dak}_Image_Source{target_identity_suffix}"
        copy_file(workspace, target_identity_source, target_source_image_rel, created)
        srt_id = f"srt_{offset:04d}"
        start = segment_time(segment, "start")
        end = segment_time(segment, "end")
        duration = segment_time(segment, "duration") or max(0.0, end - start)
        segment_audio_rel = ""
        if has_reference_audio:
            segment_audio_rel = f"{STORYBOARD_WORKING_REL}/{dak}_Audio_Final.wav"
            extract_audio_segment(mixed_audio_path, workspace_path(workspace, segment_audio_rel), start, duration)
            created.append(segment_audio_rel)
        target_image_rel = ""
        images = [
            {"slot": "Image_New", "source_type": "", "path": ""},
            {"slot": "Image_02", "source_type": "", "path": ""},
        ]
        if offset == 1:
            target_image_rel = f"{STORYBOARD_WORKING_REL}/{dak}_Image_New{target_identity_suffix}"
            copy_file(workspace, target_identity_source, target_image_rel, created)
            images[0] = {"slot": "Image_New", "source_type": "dance_mimic_target_identity", "path": target_image_rel}
        storyboard_segments.append({
            "srt_id": srt_id,
            "dialogue_asset_key": dak,
            "dialogue": f"Dance motion segment {offset:04d}",
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
            "image_path": target_source_image_rel,
            "source_image_paths": [target_source_image_rel],
            "dance_mimic": {
                "source_segment_id": text_value(segment.get("segment_id")) or f"segment_{offset:04d}",
                "reference_video_path": target_rel,
                "provider_reference_video_path": source_rel,
                "reference_video_role": DANCE_MIMIC_REFERENCE_VIDEO_ROLE,
                "target_identity_image_path": target_source_image_rel,
                "provider_target_identity_image_path": provider_target_identity_rel,
                "source_target_identity_image_path": target_identity_rel,
                "segment_audio_source_path": segment_audio_rel,
                "privacy_grid_mode": privacy_mode == "red_grid_guide",
                "reference_video_grid_applied": bool(privacy_manifest.get("apply_to_reference_video")),
                "target_identity_grid_applied": bool(privacy_manifest.get("apply_to_target_identity_image")),
                "effective_grid_scope": text_value(privacy_manifest.get("effective_grid_scope")),
                "privacy_grid_manifest_path": PRIVACY_GRID_MANIFEST_REL if privacy_mode == "red_grid_guide" else "",
                "prompt_contract": "dance_mimic_privacy_grid_clean_output_0.1" if privacy_mode == "red_grid_guide" and text_value(privacy_manifest.get("effective_grid_scope")) != "none" else "",
            },
            "working_assets": {
                "audio": {"slot": "Audio_Final", "source_type": "dance_mimic_reference_audio", "path": segment_audio_rel} if segment_audio_rel else {"slot": "Audio_Final", "source_type": "", "path": ""},
                "images": images,
                "video": {"slot": "Video_Final", "source_type": "", "path": ""},
            },
        })
        seed_segments.append({
            "segment_id": text_value(segment.get("segment_id")) or f"segment_{offset:04d}",
            "index": offset,
            "srt_id": srt_id,
            "dialogue_asset_key": dak,
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "duration_seconds": round(duration, 3),
            "reference_video_path": target_rel,
            "provider_reference_video_path": source_rel,
            "source_face_masked_reference_video_path": source_rel,
            "target_identity_image_path": target_source_image_rel,
            "provider_target_identity_image_path": provider_target_identity_rel,
            "first_frame_image_path": target_image_rel,
            "source_target_identity_image_path": target_identity_rel,
            "segment_audio_path": segment_audio_rel,
            "segment_audio_source_path": mixed_audio_rel if segment_audio_rel else "",
            "video_generation_mode": "dance_mimic_reference_video",
            "provider": "openrouter",
            "model": "bytedance/seedance-2.0",
            "model_alias": "MaxSR2",
            "reference_mode": "input_references",
            "prompt_template": "Video_SDR2V_DanceMimic.md",
            "reference_video_role": DANCE_MIMIC_REFERENCE_VIDEO_ROLE,
            "privacy_grid_mode": privacy_mode == "red_grid_guide",
            "reference_video_grid_applied": bool(privacy_manifest.get("apply_to_reference_video")),
            "target_identity_grid_applied": bool(privacy_manifest.get("apply_to_target_identity_image")),
            "effective_grid_scope": text_value(privacy_manifest.get("effective_grid_scope")),
            "privacy_grid_manifest_path": PRIVACY_GRID_MANIFEST_REL if privacy_mode == "red_grid_guide" else "",
            "prompt_contract": "dance_mimic_privacy_grid_clean_output_0.1" if privacy_mode == "red_grid_guide" and text_value(privacy_manifest.get("effective_grid_scope")) != "none" else "",
        })
    if not storyboard_segments:
        raise ToolBlocked("reference_segments_empty", f"{SEGMENTS_MANIFEST_REL} contains no segments.")
    audio_outputs: dict[str, str] = {}
    for source_rel, name in ((MIXED_AUDIO_REL, "Audio_Reference_Mixed.wav"), (VOCAL_AUDIO_REL, "Audio_Reference_Vocal.wav")):
        source = workspace / source_rel
        if source.exists() and source.stat().st_size > 0:
            target_rel = f"{STORYBOARD_AUDIO_ASSETS_REL}/{name}"
            copy_file(workspace, source, target_rel, created)
            audio_outputs[name] = target_rel
        else:
            result["warnings"].append({"code": "optional_audio_missing", "message": f"Optional audio asset missing: {source_rel}"})
    total_start = min(item["start"] for item in storyboard_segments)
    total_end = max(item["end"] for item in storyboard_segments)
    storyboard = {
        "schema_version": "analysis_v1_srt_storyboard_0.2",
        "workflow_id": WORKFLOW_ID,
        "source_type": "dance_mimic_v1_storyboard",
        "task_summary": "DanceMimic reference motion storyboard",
        "video_formula": "dance_mimic_motion_reference",
        "shots": [{
            "shot_id": "shot_001",
            "shot_name": "DanceMimic reference motion",
            "start": round(total_start, 3),
            "end": round(total_end, 3),
            "duration": round(total_end - total_start, 3),
            "scenes": [{
                "scene_id": "scene_001",
                "scene_name": "Reference dance motion",
                "start": round(total_start, 3),
                "end": round(total_end, 3),
                "duration": round(total_end - total_start, 3),
                "dialogue_items": storyboard_segments,
            }],
        }],
    }
    seed = {
        "schema_version": "dance_mimic_v1_storyboard_seed_0.1",
        "workflow_id": WORKFLOW_ID,
        "task_id": args.task_id,
        "session_id": args.session_id,
        "source_video_path": text_value(variables.get("source_video_path")),
        "target_identity_image_path": target_identity_rel,
        "reference_media_manifest_path": REFERENCE_MANIFEST_REL,
        "reference_segments_manifest_path": SEGMENTS_MANIFEST_REL,
        "mixed_audio_path": audio_outputs.get("Audio_Reference_Mixed.wav", ""),
        "vocal_audio_path": audio_outputs.get("Audio_Reference_Vocal.wav", ""),
        "segments": seed_segments,
        "warnings": result["warnings"],
    }
    write_json(workspace / STORYBOARD_REL, storyboard)
    write_json(workspace / STORYBOARD_SEED_REL, seed)
    created.extend([STORYBOARD_REL, STORYBOARD_SEED_REL])
    result["inputs"] = {"variables": VARIABLES_REL, "target_identity_image": target_identity_rel, "reference_media_manifest": REFERENCE_MANIFEST_REL, "reference_segments_manifest": SEGMENTS_MANIFEST_REL}
    result["outputs"] = {"srt_storyboard_path": STORYBOARD_REL, "storyboard_seed_path": STORYBOARD_SEED_REL, "video_asset_count": len(seed_segments), "audio_assets": audio_outputs}
    result["summary"] = {"segment_count": len(seed_segments), "video_asset_count": len(seed_segments)}
    clear_stale_items(workspace, result, ["03_storyboard_standard_task_build", "storyboard_reference_video_assets"])
    return result


def parse_args(tool_key: str, argv: list[str] | None = None) -> Args:
    parser = argparse.ArgumentParser(description=f"DanceMimic_V1 {TOOL_META[tool_key]['tool_id']}")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--workflow-id", default=WORKFLOW_ID)
    parser.add_argument("--task-id", type=int)
    parser.add_argument("--session-id", type=int)
    parser.add_argument("--attempt-id", type=int)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--source-video-path", default="")
    parser.add_argument("--target-identity-image-path", default="")
    parser.add_argument("--face-detections-manifest", default="")
    parser.add_argument("--reference-privacy-mode", default="")
    parser.add_argument("--apply-privacy-grid-to-reference-video", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--apply-privacy-grid-to-target-identity-image", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--target-video-seconds", type=float)
    parser.add_argument("--minimum-video-seconds", type=float)
    parser.add_argument("--block-on-face-not-detected", action="store_true")
    ns, _unknown = parser.parse_known_args(argv)
    return Args(**vars(ns))


def run_tool(tool_key: str, argv: list[str] | None = None) -> int:
    args = parse_args(tool_key, argv)
    workspace = Path(args.workspace).expanduser().resolve()
    prepared: list[str] = []
    try:
        if not workspace.exists() or not workspace.is_dir():
            raise ToolBlocked("workspace_missing", f"Workspace does not exist: {workspace}")
        runner = {"00": run_00, "01": run_01, "02": run_02, "03": run_03}[tool_key]
        result = runner(workspace, args)
    except ToolBlocked as exc:
        tool_dir = TOOL_META[tool_key]["tool_dir"]
        if workspace.exists():
            prepared = ensure_dirs(workspace, tool_dir)
        result = blocked_result(tool_key, workspace, args, exc, prepared)
    except Exception as exc:
        tool_dir = TOOL_META[tool_key]["tool_dir"]
        if workspace.exists():
            prepared = ensure_dirs(workspace, tool_dir)
        result = blocked_result(tool_key, workspace, args, ToolFailed("unexpected_error", str(exc)), prepared)
    return finalize_result(tool_key, workspace, result, print_json=args.print_json)
