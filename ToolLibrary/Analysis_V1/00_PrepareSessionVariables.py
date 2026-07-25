from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from OpenCrew.ToolLibrary.Analysis_V1 import DEFAULT_DATABASE_URL_ENV, DEFAULT_OPENCREW_DATABASE_URL, DEFAULT_WORKFLOW_ID
except Exception:
    DEFAULT_WORKFLOW_ID = "openclip_analysis"
    DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
    DEFAULT_OPENCREW_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"

TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
try:
    from opencrew_runtime_secrets import resolve_secret_value
except Exception:  # pragma: no cover - keeps standalone legacy runs usable
    def resolve_secret_value(api_key_ref: str, legacy_value: str = "") -> str:
        return str(legacy_value or "").strip()

TOOL_NAME = "00_PrepareSessionVariables"
TOOL_VERSION = "0.1.0"
WORKFLOW_ID = DEFAULT_WORKFLOW_ID
DATABASE_URL_ENV = DEFAULT_DATABASE_URL_ENV
OPENCREW_ROOT = Path(os.environ.get("OPENCREW_DATA_DIR") or (Path.home() / ".opencrew")).expanduser()
CONTEXT_DIR_NAME = "SessionContext"
TOOL_DIR_NAME = "S1_00_PrepareSessionVariables"
SESSION_REPORT_DIR_NAME = "SessionReport"
SESSION_OUTPUT_DIR_NAME = "SessionOutput"
# `inbox`, `meta`, `outbox`, and non-Variables content under SessionContext are
# owned by the application or downstream tools. A forced 00 rerun must not
# remove them: SessionContext/Consistency contains task-level identity assets,
# while meta contains live thumbnail state.
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
OUTPUT_VARIABLES_REL = f"{TOOL_DIR_NAME}/Output/Variables.json"
SOURCE_VIDEO_REL = f"{CONTEXT_DIR_NAME}/Video_Source.mp4"
SOURCE_SCRIPT_REL = "SessionOutput/subtitle/source_script.txt"
SOURCE_SRT_ITEMS_REL = "SessionOutput/subtitle/final_srt_frame_items.json"
RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SUPPORTED_SOURCE_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
DEFAULT_ASR_CONFIG_TABLE = "tool_asr_provider_configs"
DEFAULT_ASR_CONFIG_NAME = "default_asr_provider"
DEFAULT_TTS_CONFIG_TABLE = "tool_media_provider_configs"
DEFAULT_TTS_PROVIDER = "google"
DEFAULT_GEMINI_BUILDER_G_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_GEMINI_SCENE_PROFILE_MODEL = "gemini-3.1-flash-image"
DEFAULT_MEDIA_CONFIG_TABLE = "tool_media_provider_configs"
MEDIA_DEFAULT_KINDS = ("image", "video", "lipsync")
DEFAULT_STORYBOARD_QUICK_CONFIG = {
    "enabled": True,
    "target_scene_seconds": 8.0,
    "target_shot_seconds": 16.0,
    "split_tolerance_seconds": 2.0,
    "language_boundary_mode": "balanced",
}


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class DatabaseError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Args:
    workflow_id: str
    task_id: int
    session_id: int | None
    attempt_id: int | None
    attempt_mode: str
    clip_mode: str
    selected_scheme: str
    source_video: str
    database_url: str
    allow_cloud_asr_data_transfer: bool
    force: bool
    print_json: bool


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def classify_database_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if "password authentication failed" in text or "authentication failed" in text or "fe_sendauth" in text:
        return "database_auth_failed"
    if "connection refused" in text:
        return "database_connection_refused"
    if "operation not permitted" in text or "eperm" in text or "permission denied" in text or "network is unreachable" in text:
        return "database_network_blocked"
    return "database_query_failed"


def postgres_connect(database_url: str) -> Any:
    normalized_url = normalize_database_url(database_url)
    try:
        import psycopg  # type: ignore

        conn = psycopg.connect(normalized_url, connect_timeout=5)
        conn.execute("SET client_encoding TO 'UTF8'")
        return conn
    except ImportError:
        try:
            import psycopg2  # type: ignore
        except ImportError as exc:
            raise DatabaseError(
                "database_driver_missing",
                "PostgreSQL driver is not available. Install psycopg or psycopg2 in the OpenCrew runtime.",
            ) from exc
        try:
            conn = psycopg2.connect(normalized_url, connect_timeout=5)
            conn.set_client_encoding("UTF8")
            return conn
        except Exception as exc:
            raise DatabaseError(classify_database_exception(exc), str(exc)) from exc
    except Exception as exc:
        raise DatabaseError(classify_database_exception(exc), str(exc)) from exc


def column_exists(conn: Any, table_name: str, column_name: str) -> bool:
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
SELECT 1
FROM information_schema.columns
WHERE table_name = %s AND column_name = %s
LIMIT 1
""",
                (table_name, column_name),
            )
            return bool(cursor.fetchone())
    except Exception as exc:
        raise DatabaseError(classify_database_exception(exc), str(exc)) from exc


def fetch_task_context(database_url: str, task_id: int) -> dict[str, Any]:
    conn = postgres_connect(database_url)
    try:
        storyboard_quick_config_expr = (
            "t.storyboard_quick_config_json"
            if column_exists(conn, "openclip_tasks", "storyboard_quick_config_json")
            else "NULL AS storyboard_quick_config_json"
        )
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
SELECT
  t.id AS task_id,
  t.session_id,
  t.status AS task_status,
  t.reference_video_path,
  t.simple_prompt,
  t.final_prompt,
  t.rewrite_simple_prompt,
  t.rewrite_final_prompt,
  t.storyboard_simple_prompt,
  t.storyboard_final_prompt,
  {storyboard_quick_config_expr},
  t.current_prompt_version_id,
  t.current_skill_version_id,
  t.latest_attempt_id,
  t.run_model_provider,
  t.run_model_id,
  s.id AS opencrew_session_id,
  s.opencode_session_id,
  s.workspace_dir,
  s.status AS session_status
FROM openclip_tasks t
JOIN sessions s ON s.id = t.session_id
WHERE t.id = %s
LIMIT 1
""",
                (task_id,),
            )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
        if not row:
            raise BlockedError("task_not_found", f"OpenClip Task #{task_id} was not found.")
        return dict(zip(columns, row))
    except BlockedError:
        raise
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError(classify_database_exception(exc), str(exc)) from exc
    finally:
        conn.close()


def fetch_latest_attempt_id(database_url: str, task_id: int) -> int | None:
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
SELECT id
FROM openclip_attempts
WHERE task_id = %s
ORDER BY id DESC
LIMIT 1
""",
                (task_id,),
            )
            row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except Exception as exc:
        if isinstance(exc, DatabaseError):
            raise
        raise DatabaseError(classify_database_exception(exc), str(exc)) from exc
    finally:
        conn.close()


def default_asr_public_config() -> dict[str, Any]:
    return {
        "config_name": DEFAULT_ASR_CONFIG_NAME,
        "provider": "aliyun_bailian_fun_asr",
        "model": "fun-asr",
        "language": "zh",
        "api_url": "dashscope://audio/asr/transcription",
        "api_key_ref": "aliyun_bailian_fun_asr_key",
        "has_api_key": False,
        "source": "public_default",
    }


def fetch_default_asr_public_config(database_url: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
SELECT name, provider, model, language, api_url, api_key_ciphertext, api_key_ref, enabled, updated_at
FROM {DEFAULT_ASR_CONFIG_TABLE}
WHERE enabled = true
ORDER BY (name = %s) DESC, priority ASC, id ASC
LIMIT 1
""",
                (DEFAULT_ASR_CONFIG_NAME,),
            )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
    except Exception as exc:
        text = str(exc).lower()
        if "does not exist" in text or "undefinedtable" in text:
            config = default_asr_public_config()
            warnings.append({
                "code": "asr_config_table_missing",
                "message": f"{DEFAULT_ASR_CONFIG_TABLE} is missing; public default ASR config was written without an API key.",
            })
            return config, warnings
        raise DatabaseError(classify_database_exception(exc), str(exc)) from exc
    finally:
        conn.close()

    if not row:
        config = default_asr_public_config()
        warnings.append({
            "code": "asr_default_config_missing",
            "message": "No enabled ASR provider config was found; public default ASR config was written without an API key.",
        })
        return config, warnings

    data = dict(zip(columns, row))
    provider = decode_text(data.get("provider")).strip() or "aliyun_bailian_fun_asr"
    api_key_ref = decode_text(data.get("api_key_ref")).strip()
    legacy_key = decode_text(data.get("api_key_ciphertext")).strip()
    return {
        "config_name": DEFAULT_ASR_CONFIG_NAME,
        "provider": provider,
        "model": decode_text(data.get("model")).strip() or ("small" if provider == "local_whisper" else "fun-asr"),
        "language": decode_text(data.get("language")).strip() or "zh",
        "api_url": decode_text(data.get("api_url")).strip(),
        "api_key_ref": api_key_ref,
        "has_api_key": bool(resolve_secret_value(api_key_ref, legacy_key)),
        "source": f"postgres:{DEFAULT_ASR_CONFIG_TABLE}",
    }, warnings


def parse_extra_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = decode_text(value).strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def normalize_storyboard_quick_config(value: Any) -> tuple[dict[str, Any], bool]:
    raw = parse_extra_json(value)

    def positive_number(key: str, fallback: float) -> tuple[float, bool]:
        try:
            parsed = float(raw.get(key))
            return (parsed, False) if parsed > 0 else (fallback, True)
        except Exception:
            return fallback, True

    scene, scene_defaulted = positive_number("target_scene_seconds", DEFAULT_STORYBOARD_QUICK_CONFIG["target_scene_seconds"])
    shot, shot_defaulted = positive_number("target_shot_seconds", DEFAULT_STORYBOARD_QUICK_CONFIG["target_shot_seconds"])
    tolerance, tolerance_defaulted = positive_number("split_tolerance_seconds", DEFAULT_STORYBOARD_QUICK_CONFIG["split_tolerance_seconds"])
    mode = decode_text(raw.get("language_boundary_mode")).strip().lower()
    mode_defaulted = mode not in {"strict", "balanced", "loose"}
    if mode_defaulted:
        mode = DEFAULT_STORYBOARD_QUICK_CONFIG["language_boundary_mode"]
    config = {
        "enabled": raw.get("enabled") is not False,
        "target_scene_seconds": max(1.0, scene),
        "target_shot_seconds": max(1.0, shot),
        "split_tolerance_seconds": max(0.0, tolerance),
        "language_boundary_mode": mode,
        "source": "openclip_tasks.storyboard_quick_config_json" if raw else "default",
    }
    defaulted = not raw or scene_defaulted or shot_defaulted or tolerance_defaulted or mode_defaulted
    return config, defaulted


def default_tts_public_config() -> dict[str, Any]:
    return {
        "kind": "tts",
        "provider": DEFAULT_TTS_PROVIDER,
        "model": DEFAULT_GEMINI_BUILDER_G_TTS_MODEL,
        "builder": "Builder-G",
        "builder_g_default_model": DEFAULT_GEMINI_BUILDER_G_TTS_MODEL,
        "scene_profile_model": DEFAULT_GEMINI_SCENE_PROFILE_MODEL,
        "scene_profile_default_model": DEFAULT_GEMINI_SCENE_PROFILE_MODEL,
        "api_key_ref": "google_gemini_tts_key",
        "has_api_key": False,
        "source": "public_default",
    }


def default_media_public_config(kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "provider": "",
        "model": "",
        "enabled": False,
        "active": False,
        "api_key_ref": "",
        "has_api_key": False,
        "source": "public_default_missing",
        "extra": {},
        "extra_json": {},
    }


def normalize_media_public_config(kind: str, provider: str, model: str, extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if kind != "lipsync" or provider.lower() not in {"heygen", "hey-gen"}:
        return model, extra
    normalized = model
    selected = model
    if model == "heygen-lipsync-speed":
        normalized = "speed"
    elif model == "heygen-lipsync-precision":
        normalized = "precision"
    if normalized in {"speed", "precision"}:
        next_extra = dict(extra)
        next_extra.setdefault("selected_model", selected)
        next_extra.setdefault("mode", normalized)
        return normalized, next_extra
    return model, extra


def fetch_default_tts_public_config(database_url: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
SELECT kind, provider, model, api_key_ciphertext, api_key_ref, enabled, active, updated_at, extra_json
FROM {DEFAULT_TTS_CONFIG_TABLE}
WHERE kind = %s AND provider IN ('google', 'gemini') AND enabled = true
ORDER BY active DESC, id ASC
LIMIT 1
""",
                ("tts",),
            )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
    except Exception as exc:
        text = str(exc).lower()
        if "does not exist" in text or "undefinedtable" in text:
            config = default_tts_public_config()
            warnings.append({
                "code": "tts_config_table_missing",
                "message": f"{DEFAULT_TTS_CONFIG_TABLE} is missing; public default Gemini Builder-G TTS config was written without an API key.",
            })
            return config, warnings
        raise DatabaseError(classify_database_exception(exc), str(exc)) from exc
    finally:
        conn.close()

    if not row:
        config = default_tts_public_config()
        warnings.append({
            "code": "tts_default_config_missing",
            "message": "No enabled Gemini TTS provider config was found; public default Gemini Builder-G TTS config was written without an API key.",
        })
        return config, warnings

    data = dict(zip(columns, row))
    extra = parse_extra_json(data.get("extra_json"))
    provider = decode_text(data.get("provider")).strip() or DEFAULT_TTS_PROVIDER
    model = decode_text(data.get("model")).strip() or DEFAULT_GEMINI_BUILDER_G_TTS_MODEL
    api_key_ref = decode_text(data.get("api_key_ref")).strip()
    legacy_key = decode_text(data.get("api_key_ciphertext")).strip()
    has_api_key = bool(resolve_secret_value(api_key_ref, legacy_key))
    scene_model = (
        decode_text(extra.get("scene_profile_model")).strip()
        or decode_text(extra.get("builder_g_scene_profile_model")).strip()
        or DEFAULT_GEMINI_SCENE_PROFILE_MODEL
    )
    return {
        "kind": decode_text(data.get("kind")).strip() or "tts",
        "provider": provider,
        "model": model,
        "builder": "Builder-G",
        "builder_g_default_model": DEFAULT_GEMINI_BUILDER_G_TTS_MODEL,
        "scene_profile_model": scene_model,
        "scene_profile_default_model": DEFAULT_GEMINI_SCENE_PROFILE_MODEL,
        "api_key_ref": api_key_ref,
        "has_api_key": has_api_key,
        "source": f"postgres:{DEFAULT_TTS_CONFIG_TABLE}",
    }, warnings


def fetch_default_media_public_config(database_url: str, kind: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
SELECT kind, provider, model, api_key_ciphertext, api_key_ref, enabled, active, updated_at, extra_json
FROM {DEFAULT_MEDIA_CONFIG_TABLE}
WHERE kind = %s AND enabled = true
ORDER BY active DESC, id ASC
LIMIT 1
""",
                (kind,),
            )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
    except Exception as exc:
        text = str(exc).lower()
        if "does not exist" in text or "undefinedtable" in text:
            config = default_media_public_config(kind)
            warnings.append({
                "code": f"{kind}_config_table_missing",
                "message": f"{DEFAULT_MEDIA_CONFIG_TABLE} is missing; public default {kind} config was written without provider/model/API key.",
            })
            return config, warnings
        raise DatabaseError(classify_database_exception(exc), str(exc)) from exc
    finally:
        conn.close()

    if not row:
        config = default_media_public_config(kind)
        warnings.append({
            "code": f"{kind}_default_config_missing",
            "message": f"No enabled {kind} media provider config was found; public default {kind} config was written without provider/model/API key.",
        })
        return config, warnings

    data = dict(zip(columns, row))
    extra = parse_extra_json(data.get("extra_json"))
    api_key_ref = decode_text(data.get("api_key_ref")).strip()
    legacy_key = decode_text(data.get("api_key_ciphertext")).strip()
    provider = decode_text(data.get("provider")).strip()
    model = decode_text(data.get("model")).strip()
    model, extra = normalize_media_public_config(kind, provider, model, extra)
    return {
        "kind": decode_text(data.get("kind")).strip() or kind,
        "provider": provider,
        "model": model,
        "enabled": bool(data.get("enabled")),
        "active": bool(data.get("active")),
        "api_key_ref": api_key_ref,
        "has_api_key": bool(resolve_secret_value(api_key_ref, legacy_key)),
        "source": f"postgres:{DEFAULT_MEDIA_CONFIG_TABLE}",
        "extra": extra,
        "extra_json": extra,
        "updated_at": decode_text(data.get("updated_at")).strip(),
    }, warnings


def permission_hint() -> str:
    return (
        f"请在 Codex 本会话一次性授权 file_system read/write: {OPENCREW_ROOT}，"
        "并授权 network enabled，用于只读连接既有 OpenCrew PostgreSQL；"
        "如果后续 02_01_AudioASR 使用默认云端 ASR，还需要允许将本任务音频发送到数据库配置的云端 ASR 服务；"
        "03_01_TTSBuilderG 会从 SessionContext 选择 Gemini TTS 模型，并在运行时从数据库读取 API key 到内存；"
        "05_02_VideoPlanExecutor 会从 SessionContext 选择图片、视频和对嘴型模型，并在运行时从数据库读取 API key 到内存；"
        "04_01_SRTRewrite 和 04_02_StoryBoard 会通过 OpenCode run model 调用文本大模型。"
    )


def base_result(args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workflow_id": args.workflow_id,
        "task_id": args.task_id,
        "opencrew_session_id": None,
        "workspace_dir": "",
        "required_permissions": {
            "file_system": f"read/write {OPENCREW_ROOT} for Task-bound session workspaces",
            "network": "required for 00 database lookup, later 02 default/cloud ASR, 03_01 Gemini TTS/OpenCode run-model calls, and 04_01/04_02 OpenCode run-model calls",
            "cloud_asr_data_transfer": "required for 02 default/cloud mode because source audio is sent to the configured ASR provider",
        },
        "created_files": [],
        "prepared_directories": [],
        "cleanup_actions": [],
        "blocked_reasons": [],
        "warnings": [],
        "authorization_hint": permission_hint(),
        "updated_at": now_iso(),
    }


def add_block(result: dict[str, Any], code: str, message: str) -> None:
    result["status"] = "blocked"
    result.setdefault("blocked_reasons", []).append({"code": code, "message": message})


def ensure_workflow(args: Args) -> None:
    if args.workflow_id != WORKFLOW_ID:
        raise BlockedError("unsupported_workflow", f"Only workflow_id={WORKFLOW_ID} is supported.")


def ensure_opencrew_root_access() -> None:
    if not OPENCREW_ROOT.exists():
        raise BlockedError("opencrew_root_missing", f"{OPENCREW_ROOT} does not exist.")
    if not OPENCREW_ROOT.is_dir():
        raise BlockedError("opencrew_root_not_directory", f"{OPENCREW_ROOT} is not a directory.")
    probe = OPENCREW_ROOT / ".codex_analysis_v1_permission_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        raise BlockedError(
            "opencrew_root_not_writable",
            f"Cannot write to {OPENCREW_ROOT}: {exc}. {permission_hint()}",
        ) from exc


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def prepare_workspace_layout(workspace: Path, force: bool, result: dict[str, Any]) -> None:
    cleanup_actions = result.setdefault("cleanup_actions", [])
    if force:
        # Only reset artifacts owned by this step. Deleting the whole
        # SessionContext used to erase Consistency/HOST.png, Product.png,
        # PromptBuilder state, scripts, and other task-level inputs.
        for name in (VARIABLES_REL, TOOL_DIR_NAME):
            target = workspace / name
            if target.exists():
                try:
                    remove_path(target)
                    cleanup_actions.append({"path": name, "action": "removed_for_force_rerun"})
                except Exception as exc:
                    raise BlockedError("force_rerun_cleanup_failed", f"Cannot reset {target}: {exc}") from exc

    prepared = result.setdefault("prepared_directories", [])
    for rel in (
        CONTEXT_DIR_NAME,
        SESSION_REPORT_DIR_NAME,
        SESSION_OUTPUT_DIR_NAME,
        f"{TOOL_DIR_NAME}/Output",
        f"{TOOL_DIR_NAME}/Report",
    ):
        try:
            (workspace / rel).mkdir(parents=True, exist_ok=True)
            prepared.append(rel)
        except Exception as exc:
            raise BlockedError("workspace_layout_prepare_failed", f"Cannot create workspace directory {workspace / rel}: {exc}") from exc


def ensure_workspace_access(workspace: Path, force: bool, result: dict[str, Any]) -> None:
    if not workspace.exists():
        raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise BlockedError("workspace_not_directory", f"Workspace is not a directory: {workspace}")
    try:
        report_dir = workspace / TOOL_DIR_NAME / "Report"
        prepare_workspace_layout(workspace, force, result)
        probe = report_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        raise BlockedError("workspace_not_writable", f"Cannot write to workspace {workspace}: {exc}. {permission_hint()}") from exc


def resolve_source_video(workspace: Path, reference_path: str, source_override: str, warnings: list[dict[str, str]] | None = None) -> Path:
    raw = (source_override or reference_path or "").strip()
    if not raw:
        fallback = workspace / SOURCE_VIDEO_REL
        if fallback.is_file():
            if warnings is not None:
                warnings.append({"code": "source_video_reused", "message": f"No source path was provided; reused existing {SOURCE_VIDEO_REL}."})
            return fallback.resolve(strict=True)
        raise BlockedError("source_video_missing", "No source video path was provided by task.reference_video_path or --source-video.")
    source = Path(raw).expanduser()
    if not source.is_absolute():
        source = workspace / source
    try:
        source = source.resolve(strict=True)
    except FileNotFoundError as exc:
        fallback = workspace / SOURCE_VIDEO_REL
        if not source_override and fallback.is_file():
            if warnings is not None:
                warnings.append({"code": "source_video_reused_after_missing_reference", "message": f"{raw} was missing; reused existing {SOURCE_VIDEO_REL}."})
            return fallback.resolve(strict=True)
        raise BlockedError("source_video_not_found", f"Source video does not exist: {source}") from exc
    except Exception as exc:
        raise BlockedError("source_video_unreadable", f"Cannot resolve source video {source}: {exc}") from exc
    if not source.is_file():
        raise BlockedError("source_video_not_file", f"Source video is not a file: {source}")
    source_suffix = source.suffix.lower()
    if source_suffix not in SUPPORTED_SOURCE_VIDEO_EXTS:
        allowed = ", ".join(sorted(SUPPORTED_SOURCE_VIDEO_EXTS))
        raise BlockedError("source_video_unsupported_format", f"Source video must use one of these formats: {allowed}. Got: {source}")
    try:
        with source.open("rb") as handle:
            handle.read(1)
    except Exception as exc:
        raise BlockedError("source_video_unreadable", f"Cannot read source video {source}: {exc}") from exc
    return source


def copy_source_video(source: Path, target: Path, force: bool, warnings: list[dict[str, str]]) -> None:
    try:
        if source.resolve(strict=True) == target.resolve(strict=False):
            warnings.append({"code": "source_video_already_prepared", "message": f"Source video already exists at {SOURCE_VIDEO_REL}."})
            return
    except Exception:
        pass
    if target.exists() and not force:
        try:
            if target.stat().st_size == source.stat().st_size:
                warnings.append({"code": "existing_source_video_kept", "message": f"Existing {SOURCE_VIDEO_REL} was kept because size matches source."})
                return
        except Exception:
            pass
        raise BlockedError("target_source_video_exists", f"{SOURCE_VIDEO_REL} already exists and differs from source. Use --force to overwrite.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if source.suffix.lower() != target.suffix.lower():
            warnings.append({
                "code": "source_video_extension_normalized",
                "message": f"Copied {source.name} to internal Analysis_V1 source path {SOURCE_VIDEO_REL}.",
            })
    except Exception as exc:
        raise BlockedError("source_video_copy_failed", f"Cannot copy source video to {SOURCE_VIDEO_REL}: {exc}") from exc


def build_variables(
    args: Args,
    context: dict[str, Any],
    workspace: Path,
    source: Path | None,
    current_attempt_id: int | None,
    default_asr_config: dict[str, Any],
    default_tts_config: dict[str, Any],
    default_image_config: dict[str, Any],
    default_video_config: dict[str, Any],
    default_lipsync_config: dict[str, Any],
    storyboard_quick_config: dict[str, Any],
) -> dict[str, Any]:
    timestamp = now_iso()
    selected_tts_model = decode_text(default_tts_config.get("model")).strip() or DEFAULT_GEMINI_BUILDER_G_TTS_MODEL
    selected_scene_model = decode_text(default_tts_config.get("scene_profile_model")).strip() or DEFAULT_GEMINI_SCENE_PROFILE_MODEL
    tts_provider = decode_text(default_tts_config.get("provider")).strip() or DEFAULT_TTS_PROVIDER
    default_asr_provider = decode_text(default_asr_config.get("provider")).strip() or "aliyun_bailian_fun_asr"
    return {
        "schema_version": "analysis_v1_session_context_0.1",
        "tool_use_session_id": str(uuid.uuid4()),
        "workflow_id": WORKFLOW_ID,
        "task_id": int(context.get("task_id") or args.task_id),
        "opencrew_session_id": int(context.get("opencrew_session_id") or context.get("session_id") or 0) or None,
        "opencode_session_id": decode_text(context.get("opencode_session_id")).strip(),
        "workspace_dir": str(workspace),
        "current_attempt_id": current_attempt_id,
        "current_prompt_version_id": int(context.get("current_prompt_version_id") or 0) or None,
        "current_skill_version_id": int(context.get("current_skill_version_id") or 0) or None,
        "latest_attempt_id": int(context.get("latest_attempt_id") or 0) or None,
        "run_model_provider": decode_text(context.get("run_model_provider")).strip(),
        "run_model_id": decode_text(context.get("run_model_id")).strip(),
        "clip_mode": args.clip_mode,
        "selected_scheme": args.selected_scheme,
        "input_mode": "video" if source else "script_only",
        "source_video_path": SOURCE_VIDEO_REL if source else "",
        "reference_video_original_path": str(source) if source else "",
        "source_script_path": SOURCE_SCRIPT_REL if not source and (workspace / SOURCE_SCRIPT_REL).exists() else "",
        "source_srt_items_path": SOURCE_SRT_ITEMS_REL if not source and (workspace / SOURCE_SRT_ITEMS_REL).exists() else "",
        "default_asr_provider": default_asr_provider,
        "default_asr_config": default_asr_config,
        "default_image_config": default_image_config,
        "default_video_config": default_video_config,
        "default_lipsync_config": default_lipsync_config,
        "asr_mode": "default",
        "cloud_asr_data_transfer_allowed": bool(args.allow_cloud_asr_data_transfer),
        "cloud_asr_data_transfer_scope": "task_audio_to_configured_asr_provider" if args.allow_cloud_asr_data_transfer else "",
        "cloud_asr_data_transfer_authorized_at": timestamp if args.allow_cloud_asr_data_transfer else "",
        "default_tts_config": default_tts_config,
        "tts_mode": "builder_g",
        "gemini_builder_g_config": {
            "provider": tts_provider,
            "selected_tts_model": selected_tts_model,
            "default_tts_model": DEFAULT_GEMINI_BUILDER_G_TTS_MODEL,
            "selected_scene_profile_model": selected_scene_model,
            "default_scene_profile_model": DEFAULT_GEMINI_SCENE_PROFILE_MODEL,
            "builder": "Builder-G",
            "api_key_ref": decode_text(default_tts_config.get("api_key_ref")).strip(),
            "has_api_key": bool(default_tts_config.get("has_api_key")),
            "source": decode_text(default_tts_config.get("source")).strip(),
        },
        "simple_prompt": decode_text(context.get("simple_prompt")).strip(),
        "final_prompt": decode_text(context.get("final_prompt")).strip(),
        "rewrite_simple_prompt": decode_text(context.get("rewrite_simple_prompt")).strip(),
        "rewrite_final_prompt": decode_text(context.get("rewrite_final_prompt")).strip(),
        "storyboard_simple_prompt": decode_text(context.get("storyboard_simple_prompt")).strip(),
        "storyboard_final_prompt": decode_text(context.get("storyboard_final_prompt")).strip(),
        "rewrite_prompt": {
            "simple_prompt": decode_text(context.get("rewrite_simple_prompt")).strip(),
            "final_prompt": decode_text(context.get("rewrite_final_prompt")).strip(),
            "source": "openclip_tasks.rewrite_final_prompt",
        },
        "storyboard_prompt": {
            "simple_prompt": decode_text(context.get("storyboard_simple_prompt")).strip(),
            "final_prompt": decode_text(context.get("storyboard_final_prompt")).strip(),
            "source": "openclip_tasks.storyboard_final_prompt",
        },
        "storyboard_quick_config": storyboard_quick_config,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def prepare(args: Args) -> dict[str, Any]:
    result = base_result(args)
    try:
        ensure_workflow(args)
        ensure_opencrew_root_access()

        database_url = (args.database_url or os.environ.get(DATABASE_URL_ENV) or DEFAULT_OPENCREW_DATABASE_URL).strip()
        if not database_url:
            raise DatabaseError("missing_database_url", f"{DATABASE_URL_ENV} is required for {TOOL_NAME}.")

        context = fetch_task_context(database_url, args.task_id)
        session_id = int(context.get("opencrew_session_id") or context.get("session_id") or 0)
        result["opencrew_session_id"] = session_id or None

        if args.session_id is not None and session_id != args.session_id:
            raise BlockedError("session_id_mismatch", f"Task #{args.task_id} is bound to Session #{session_id}, not Session #{args.session_id}.")

        workspace_text = decode_text(context.get("workspace_dir")).strip()
        if not workspace_text:
            raise BlockedError("workspace_dir_missing", f"Task #{args.task_id} / Session #{session_id} has no workspace_dir.")
        workspace = Path(workspace_text).expanduser()
        result["workspace_dir"] = str(workspace)
        ensure_workspace_access(workspace, args.force, result)

        opencode_session_id = decode_text(context.get("opencode_session_id")).strip()
        if not opencode_session_id:
            raise BlockedError("opencode_session_missing", f"Session #{session_id} has no opencode_session_id.")

        if not (
            decode_text(context.get("final_prompt")).strip()
            or decode_text(context.get("rewrite_final_prompt")).strip()
            or decode_text(context.get("storyboard_final_prompt")).strip()
        ):
            raise BlockedError("final_prompt_missing", f"Task #{args.task_id} has no final_prompt, rewrite_final_prompt, or storyboard_final_prompt.")

        if args.attempt_id is not None:
            current_attempt_id = args.attempt_id
        elif args.attempt_mode == "none":
            current_attempt_id = None
        else:
            current_attempt_id = int(context.get("latest_attempt_id") or 0) or fetch_latest_attempt_id(database_url, args.task_id)
            if current_attempt_id is None:
                result["warnings"].append({"code": "latest_attempt_missing", "message": f"Task #{args.task_id} has no latest attempt."})

        source: Path | None = None
        is_script_only = (
            not (decode_text(context.get("reference_video_path")).strip() or args.source_video.strip())
            and (workspace / SOURCE_SRT_ITEMS_REL).exists()
            and (workspace / SOURCE_SCRIPT_REL).exists()
        )
        if is_script_only:
            result["warnings"].append({
                "code": "script_only_input_mode",
                "message": f"No source video was provided; using script-only inputs from {SOURCE_SRT_ITEMS_REL}.",
            })
        else:
            source = resolve_source_video(workspace, decode_text(context.get("reference_video_path")).strip(), args.source_video, result["warnings"])
            copy_source_video(source, workspace / SOURCE_VIDEO_REL, args.force, result["warnings"])

        default_asr_config, asr_warnings = fetch_default_asr_public_config(database_url)
        result["warnings"].extend(asr_warnings)
        default_tts_config, tts_warnings = fetch_default_tts_public_config(database_url)
        result["warnings"].extend(tts_warnings)
        media_configs: dict[str, dict[str, Any]] = {}
        for kind in MEDIA_DEFAULT_KINDS:
            config, media_warnings = fetch_default_media_public_config(database_url, kind)
            media_configs[kind] = config
            result["warnings"].extend(media_warnings)
        storyboard_quick_config, storyboard_quick_defaulted = normalize_storyboard_quick_config(context.get("storyboard_quick_config_json"))
        if storyboard_quick_defaulted:
            result["warnings"].append({
                "code": "storyboard_quick_config_defaulted",
                "message": "storyboard_quick_config_json was missing or invalid; defaults were written to SessionContext/Variables.json.",
            })
        variables = build_variables(
            args,
            context,
            workspace,
            source,
            current_attempt_id,
            default_asr_config,
            default_tts_config,
            media_configs["image"],
            media_configs["video"],
            media_configs["lipsync"],
            storyboard_quick_config,
        )
        write_json(workspace / VARIABLES_REL, variables)
        write_json(workspace / OUTPUT_VARIABLES_REL, variables)

        result["created_files"] = [VARIABLES_REL, OUTPUT_VARIABLES_REL, RESULT_REL] + ([] if source is None else [SOURCE_VIDEO_REL])
        result["status"] = "completed"
    except BlockedError as exc:
        add_block(result, exc.code, exc.message)
    except DatabaseError as exc:
        add_block(result, exc.code, exc.message)
    except Exception as exc:
        result["status"] = "failed"
        result["warnings"].append({"code": "unexpected_error", "message": str(exc)})
    result["updated_at"] = now_iso()
    if result.get("workspace_dir"):
        try:
            write_json(Path(str(result["workspace_dir"])) / RESULT_REL, result)
        except Exception as exc:
            result["warnings"].append({"code": "result_write_failed", "message": str(exc)})
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Prepare minimal Analysis_V1 session variables for OpenClip Analysis tools.")
    parser.add_argument("--workflow-id", default=WORKFLOW_ID)
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--session-id", type=int)
    parser.add_argument("--attempt-id", type=int)
    parser.add_argument("--attempt-mode", choices=["latest", "none"], default="latest")
    parser.add_argument("--clip-mode", choices=["virtual", "copy", "encode"], default="virtual")
    parser.add_argument("--selected-scheme", choices=["detail", "balanced", "summary"], default="detail")
    parser.add_argument("--source-video", default="")
    parser.add_argument("--database-url", default="")
    parser.add_argument(
        "--allow-cloud-asr-data-transfer",
        action="store_true",
        help="Record explicit consent for 02_01_AudioASR default/cloud mode to send this task audio to the database-configured cloud ASR provider.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(
        workflow_id=ns.workflow_id,
        task_id=ns.task_id,
        session_id=ns.session_id,
        attempt_id=ns.attempt_id,
        attempt_mode=ns.attempt_mode,
        clip_mode=ns.clip_mode,
        selected_scheme=ns.selected_scheme,
        source_video=ns.source_video,
        database_url=ns.database_url,
        allow_cloud_asr_data_transfer=bool(ns.allow_cloud_asr_data_transfer),
        force=bool(ns.force),
        print_json=bool(ns.print_json),
    )


def main(argv: list[str] | None = None) -> int:
    cli_args = argv if argv is not None else sys.argv[1:]
    if "--tool-session-root" in cli_args:
        try:
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge
        except ModuleNotFoundError:
            repo_root = str(Path(__file__).resolve().parents[2])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge

        framework_exit = maybe_run_framework_bridge(cli_args, script_path=Path(__file__), tool_name=TOOL_NAME)
        if framework_exit is not None:
            return framework_exit

    args = parse_args(cli_args)
    result = prepare(args)
    exit_code = 0 if result.get("status") == "completed" else 2 if result.get("status") == "blocked" else 1

    if args.print_json or exit_code != 0:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
