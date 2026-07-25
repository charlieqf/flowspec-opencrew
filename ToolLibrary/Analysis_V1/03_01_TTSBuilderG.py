from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import mimetypes
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None  # type: ignore


try:
    from OpenCrew.ToolLibrary.Analysis_V1 import DEFAULT_DATABASE_URL_ENV, DEFAULT_OPENCREW_DATABASE_URL
except Exception:  # pragma: no cover
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

try:
    from opencode_autoheal import is_opencode_session_not_found, recover_opencode_session_id
except Exception:  # pragma: no cover - standalone legacy fallback
    def is_opencode_session_not_found(exc: BaseException) -> bool:
        return False

    def recover_opencode_session_id(**_: Any) -> str:
        raise RuntimeError("opencode_autoheal is unavailable")


TOOL_NAME = "03_01_TTSBuilderG"
TOOL_VERSION = "0.1.0"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
DEFAULT_METADATA_REL = f"{CONTEXT_DIR_NAME}/Video_Metadata.json"
TOOL_DIR_NAME = "S5_03_01_TTSBuilderG"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_FINAL_ITEMS_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_4_final_srt_frame_items.json"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
WORKING_CONTACT_SHEET_REL = f"{TOOL_DIR_NAME}/Working/scene_profile_contact_sheet.jpg"
WORKING_RAW_DIR_REL = f"{TOOL_DIR_NAME}/Working/raw_candidates"
WORKING_FIT_DIR_REL = f"{TOOL_DIR_NAME}/Working/fitted_candidates"
OUTPUT_SCENE_PROFILE_REL = f"{TOOL_DIR_NAME}/Output/scene_profile_response.json"
OUTPUT_VOICE_PLAN_REL = f"{TOOL_DIR_NAME}/Output/voice_candidate_plan.json"
OUTPUT_SCORING_AUDIT_REL = f"{TOOL_DIR_NAME}/Output/voice_scoring_audit.json"
OUTPUT_FINAL_REL = f"{TOOL_DIR_NAME}/Output/tts_builder_candidates.json"
PROMPT_DIR_REL = f"{TOOL_DIR_NAME}/Prompt"
SCENE_PROFILE_PROMPT_REL = f"{PROMPT_DIR_REL}/00_scene_profile_prompt.md"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SESSION_FINAL_ITEMS_REL = "SessionOutput/subtitle/final_srt_frame_items.json"
SESSION_AUDIO_REFERENCE_REL = "SessionOutput/Audio_Reference.wav"
SESSION_TTS_DIR_REL = "SessionOutput/tts"
SESSION_TTS_FINAL_REL = f"{SESSION_TTS_DIR_REL}/tts_builder_candidates.json"
CONFIG_TABLE = "tool_media_provider_configs"
DEFAULT_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_VOICES = ["Aoede", "Kore", "Callirrhoe", "Achernar", "Sulafat", "Vindemiatrix"]
VOICE_TEST_ROLES = {
    "Aoede": "自然、轻松、接近日常口播",
    "Kore": "清楚、平稳、不过度强调",
    "Callirrhoe": "明亮、柔和、保持克制",
    "Achernar": "成熟、稳一点、保持真实说话感",
    "Sulafat": "偏年轻、轻快但不要兴奋",
    "Vindemiatrix": "有起伏但需要压低表演感",
}
SECRET_PATTERNS = (
    "postgresql://",
    "postgresql+psycopg://",
    "password",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "bearer ",
    "cookie",
)


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(RuntimeError):
    pass


class DatabaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Args:
    workspace: str
    mode: str
    scene_profile_mode: str
    tts_model: str
    scene_model: str
    voices: str
    target_duration: float
    quick_duration: float
    reference_start: float
    reference_duration: float
    top_voices: int
    final_count: int
    max_scene_frames: int
    database_url: str
    database_url_env: str
    force: bool
    resume: bool
    force_regenerate_prompts: bool
    print_json: bool


@dataclass(frozen=True)
class RunModelConfig:
    provider: str
    model: str
    source: str


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return json_safe(value.tolist())
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def write_text_if_needed(path: Path, text: str, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8").strip() and not force:
        return
    path.write_text(text, encoding="utf-8")


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def relpath(path: Path | str, workspace: Path) -> str:
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser() if raw_workspace else Path.cwd()
    try:
        return workspace.resolve()
    except Exception:
        return workspace.absolute()


def find_binary(name: str) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    toollib_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / ".bin" / name,
        repo_root / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
        toollib_root / ".bin" / name,
        toollib_root / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which(name) or name


def run_cmd(cmd: list[str], timeout: int = 120) -> str:
    result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ToolError((result.stderr or result.stdout or " ".join(cmd))[-3000:])
    return result.stdout.strip()


def media_duration(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        value = run_cmd([find_binary("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], timeout=30)
        duration = round(float(value), 3)
        if duration > 0:
            return duration
    except Exception:
        pass
    try:
        with wave.open(str(path), "rb") as reader:
            return round(reader.getnframes() / float(reader.getframerate() or 1), 3)
    except Exception:
        return 0.0


def wav_from_pcm(pcm: bytes, sample_rate: int = 24000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    out = io.BytesIO()
    out.write(b"RIFF")
    out.write(struct.pack("<I", 36 + len(pcm)))
    out.write(b"WAVEfmt ")
    out.write(struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample))
    out.write(b"data")
    out.write(struct.pack("<I", len(pcm)))
    out.write(pcm)
    return out.getvalue()


def normalize_database_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def resolve_database_url(args: Args) -> str:
    return str(args.database_url or "") or os.environ.get(args.database_url_env or DEFAULT_DATABASE_URL_ENV, "") or DEFAULT_OPENCREW_DATABASE_URL


def decode_db_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8", errors="replace").strip()
    return str(value or "").strip()


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

        conn = psycopg.connect(normalized_url, connect_timeout=8)
        conn.execute("SET client_encoding TO 'UTF8'")
        return conn
    except ImportError:
        try:
            import psycopg2  # type: ignore
        except ImportError as exc:
            raise DatabaseError("database_driver_missing") from exc
        try:
            conn = psycopg2.connect(normalized_url, connect_timeout=8)
            conn.set_client_encoding("UTF8")
            return conn
        except Exception as exc:
            raise DatabaseError(classify_database_exception(exc)) from exc
    except Exception as exc:
        raise DatabaseError(classify_database_exception(exc)) from exc


def fetch_google_api_key_from_db(args: Args, kind: str, provider: str, model: str) -> str:
    provider = provider.strip()
    provider_clause = "provider = %s" if provider in {"google", "gemini"} else "provider IN ('google', 'gemini')"
    params: list[Any] = [kind]
    if provider in {"google", "gemini"}:
        params.append(provider)
    params.append(model.strip())
    sql = f"""
SELECT api_key_ref, api_key_ciphertext
FROM {CONFIG_TABLE}
WHERE kind = %s AND {provider_clause} AND enabled = TRUE
ORDER BY (model = %s) DESC, active DESC, id ASC
LIMIT 1
"""
    conn = postgres_connect(resolve_database_url(args))
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return ""
    api_key_ref = decode_db_value(row[0]).strip()
    legacy_key = decode_db_value(row[1] if len(row) > 1 else "").strip()
    return resolve_secret_value(api_key_ref, legacy_key)


def load_google_api_key(args: Args, kind: str = "tts", provider: str = "", model: str = "") -> str:
    env_key = os.environ.get("OPENCREW_TTS_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    env_provider = os.environ.get("OPENCREW_TTS_PROVIDER", "").strip()
    if env_key and env_provider in {"", "google", "gemini"}:
        return env_key
    try:
        key = fetch_google_api_key_from_db(args, kind, provider, model)
        if not key and kind == "image":
            key = fetch_google_api_key_from_db(args, "tts", provider, model)
    except DatabaseError as exc:
        raise BlockedError(str(exc) or "database_query_failed", f"Cannot read Gemini/Google API key from {CONFIG_TABLE}: {exc.__cause__ or exc}") from exc
    if not key:
        raise BlockedError("gemini_api_key_missing", f"No enabled Google/Gemini {kind} API key found in {CONFIG_TABLE}.")
    return key


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def resolve_runtime_config(args: Args, variables: dict[str, Any]) -> dict[str, str]:
    default_tts = dict_value(variables.get("default_tts_config"))
    builder_g = dict_value(variables.get("gemini_builder_g_config"))
    provider = (
        text_value(builder_g.get("provider"))
        or text_value(default_tts.get("provider"))
        or "google"
    )
    if provider not in {"google", "gemini"}:
        raise BlockedError("unsupported_tts_provider", f"03_01_TTSBuilderG only supports Google/Gemini TTS configs, but Variables selected provider={provider}.")
    tts_model = (
        text_value(args.tts_model)
        or text_value(builder_g.get("selected_tts_model"))
        or text_value(default_tts.get("model"))
        or text_value(builder_g.get("default_tts_model"))
        or text_value(default_tts.get("builder_g_default_model"))
        or DEFAULT_TTS_MODEL
    )
    return {
        "provider": provider,
        "tts_model": tts_model,
        "source": text_value(builder_g.get("source")) or text_value(default_tts.get("source")) or "tool_default",
    }


def resolve_scene_profile_run_model_config(variables: dict[str, Any]) -> RunModelConfig:
    provider = text_value(variables.get("run_model_provider"))
    model = text_value(variables.get("run_model_id"))
    if not provider or not model:
        raise BlockedError(
            "run_model_config_missing",
            "03_01_TTSBuilderG Scene_Profile requires run_model_provider/run_model_id in SessionContext/Variables.json.",
        )
    return RunModelConfig(provider=provider, model=model, source="SessionContext/Variables.json:run_model")


def fetch_opencode_runtime(args: Args) -> dict[str, str]:
    sql = """
SELECT base_url, auth_username, auth_password
FROM opencode_runtime
WHERE id = 1
LIMIT 1
"""
    try:
        conn = postgres_connect(resolve_database_url(args))
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
        finally:
            conn.close()
    except DatabaseError as exc:
        raise BlockedError(str(exc) or "database_query_failed", f"Cannot read OpenCode runtime from database: {exc.__cause__ or exc}") from exc
    if not row:
        raise BlockedError("opencode_runtime_missing", "OpenCode runtime is missing. Please reconnect OpenCode in OpenCrew Step 1.")
    base_url = decode_db_value(row[0]).rstrip("/")
    username = decode_db_value(row[1])
    password = decode_db_value(row[2])
    if not base_url or not username or not password:
        raise BlockedError("opencode_runtime_incomplete", "OpenCode runtime is incomplete. Please reconnect OpenCode in OpenCrew Step 1.")
    return {"base_url": base_url, "username": username, "password": password}


def opencode_request(runtime: dict[str, str], method: str, path: str, payload: dict[str, Any] | None, directory: str, timeout: int = 120) -> Any:
    query = urllib.parse.urlencode({"directory": directory})
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    token = base64.b64encode(f"{runtime['username']}:{runtime['password']}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        f"{runtime['base_url']}{path}?{query}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ToolError(f"OpenCode HTTP {exc.code}: {detail}") from exc


def now_ms() -> int:
    return int(time.time() * 1000)


def last_completed_assistant(messages: list[dict[str, Any]], started_after: int) -> str:
    for message in reversed(messages):
        info = message.get("info") or {}
        if info.get("role") != "assistant":
            continue
        completed = int(((info.get("time") or {}).get("completed") or 0) or 0)
        if completed < started_after:
            continue
        texts = [str(part.get("text") or "") for part in (message.get("parts") or []) if part.get("type") == "text"]
        text = "\n".join([item.strip() for item in texts if item.strip()]).strip()
        if text:
            return text
    return ""


def image_file_part(path: Path, workspace: Path) -> dict[str, str]:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    try:
        filename = path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        filename = path.name
    return {"type": "file", "mime": mime, "filename": filename, "url": f"data:{mime};base64,{encoded}"}


def call_scene_profile_run_model(args: Args, variables: dict[str, Any], config: RunModelConfig, prompt_path: Path, contact_sheet_path: Path, workspace: Path) -> dict[str, Any]:
    session_id = text_value(variables.get("opencode_session_id"))
    if not session_id:
        raise BlockedError("opencode_session_id_missing", "SessionContext/Variables.json is missing opencode_session_id.")
    runtime = fetch_opencode_runtime(args)
    prompt_text = prompt_path.read_text(encoding="utf-8")
    directory = text_value(variables.get("workspace_dir")) or str(workspace)
    payload = {
        "parts": [
            {"type": "text", "text": prompt_text},
            image_file_part(contact_sheet_path, workspace),
        ],
        "model": {"providerID": config.provider, "modelID": config.model},
    }
    started_at = now_ms()
    try:
        opencode_request(runtime, "POST", f"/session/{urllib.parse.quote(session_id, safe='')}/prompt_async", payload, directory, timeout=30)
    except ToolError as exc:
        if not is_opencode_session_not_found(exc):
            raise
        try:
            session_id = recover_opencode_session_id(
                runtime=runtime,
                variables=variables,
                workspace=workspace,
                request_func=opencode_request,
                database_url=resolve_database_url(args),
                title=f"Analysis_V1 task {variables.get('task_id') or ''} scene profile".strip(),
            )
        except Exception as repair_exc:
            raise ToolError(f"OpenCode session was missing and automatic repair failed: {repair_exc}") from exc
        opencode_request(runtime, "POST", f"/session/{urllib.parse.quote(session_id, safe='')}/prompt_async", payload, directory, timeout=30)
    deadline = time.time() + 300
    while time.time() < deadline:
        messages = opencode_request(runtime, "GET", f"/session/{urllib.parse.quote(session_id, safe='')}/message", None, directory, timeout=30) or []
        assistant_text = last_completed_assistant(messages, started_at)
        if assistant_text:
            return parse_json_from_text(assistant_text)
        time.sleep(1)
    raise ToolError("OpenCode run model timed out before returning a completed Scene_Profile message.")


def validate_workspace(workspace: Path) -> None:
    if not workspace.exists():
        raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise BlockedError("workspace_not_directory", f"Workspace is not a directory: {workspace}")


def load_variables(workspace: Path) -> dict[str, Any]:
    path = workspace / VARIABLES_REL
    if not path.exists():
        raise BlockedError("variables_missing", f"Required SessionContext file is missing: {VARIABLES_REL}.")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    return payload


def load_final_items(workspace: Path) -> dict[str, Any]:
    path = workspace / SESSION_FINAL_ITEMS_REL
    if not path.exists():
        raise BlockedError("final_srt_frame_items_missing", f"Required final SRT frame JSON is missing: {SESSION_FINAL_ITEMS_REL}. Run 02_02_VideoSRTFrame.py first.")
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise BlockedError("final_srt_frame_items_invalid", f"{SESSION_FINAL_ITEMS_REL} must contain a JSON object with items.")
    if not payload["items"]:
        raise BlockedError("final_srt_frame_items_empty", f"{SESSION_FINAL_ITEMS_REL} contains no dialogue items.")
    return payload


def resolve_reference_audio(workspace: Path, args: Args) -> Path:
    path = workspace / SESSION_AUDIO_REFERENCE_REL
    if not path.exists() or not path.is_file():
        raise BlockedError("reference_audio_missing", f"Required reference audio is missing: {SESSION_AUDIO_REFERENCE_REL}. Run 02_01_AudioASR.py first.")
    reference_start = max(0.0, float(args.reference_start or 0.0))
    reference_duration = float(args.reference_duration or 0.0)
    if reference_duration > 0:
        selected = workspace / f"{TOOL_DIR_NAME}/Working/Audio_Reference_Selected.wav"
        selected.parent.mkdir(parents=True, exist_ok=True)
        run_cmd([
            find_binary("ffmpeg"),
            "-y",
            "-ss",
            f"{reference_start:.3f}",
            "-t",
            f"{reference_duration:.3f}",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            str(selected),
        ], timeout=120)
        if selected.exists() and selected.stat().st_size > 0:
            return selected
    return path


def ensure_dirs(workspace: Path) -> None:
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        PROMPT_DIR_REL,
        f"{TOOL_DIR_NAME}/Report",
        WORKING_RAW_DIR_REL,
        WORKING_FIT_DIR_REL,
        SESSION_TTS_DIR_REL,
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": True,
        "requires_model_calls": True,
        "model_call_policy": {
            "scene_profile_model_calls": "at_most_one",
            "tts_prompt_policy": "all_model_prompts_must_be_written_to_prompt_dir_and_read_from_files",
            "hidden_prompt_concatenation": "forbidden",
        },
        "inputs": {},
        "outputs": {},
        "counts": {},
        "created_files": [],
        "prepared_directories": [],
        "cleanup_actions": [],
        "warnings": [],
        "blocked_reasons": [],
        "force": bool(args.force),
        "resume": bool(args.resume),
        "updated_at": now_iso(),
    }


def add_block(result: dict[str, Any], code: str, message: str) -> None:
    result["status"] = "blocked"
    result.setdefault("blocked_reasons", []).append({"code": code, "message": message})


def scan_for_sensitive_output(payload: dict[str, Any]) -> list[dict[str, str]]:
    text = json.dumps(payload, ensure_ascii=False).lower()
    return [{"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"} for pattern in SECRET_PATTERNS if pattern in text]


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    for rel in (TOOL_DIR_NAME, SESSION_TTS_FINAL_REL):
        path = workspace / rel
        if path.exists():
            remove_path(path)
            result.setdefault("cleanup_actions", []).append({"path": rel, "action": "removed_for_force_rerun"})
    tts_dir = workspace / SESSION_TTS_DIR_REL
    if tts_dir.exists():
        for path in tts_dir.glob("tts_builder_candidate_*.wav"):
            path.unlink()
            result.setdefault("cleanup_actions", []).append({"path": relpath(path, workspace), "action": "removed_for_force_rerun"})


def snapshot_session_tts_outputs(workspace: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    final_path = workspace / SESSION_TTS_FINAL_REL
    if final_path.exists() and final_path.is_file():
        snapshot[SESSION_TTS_FINAL_REL] = final_path.read_bytes()
    tts_dir = workspace / SESSION_TTS_DIR_REL
    if tts_dir.exists():
        for path in tts_dir.glob("tts_builder_candidate_*.wav"):
            if path.is_file():
                snapshot[relpath(path, workspace)] = path.read_bytes()
    return snapshot


def restore_session_tts_outputs(workspace: Path, snapshot: dict[str, bytes], result: dict[str, Any]) -> None:
    if not snapshot:
        return
    for rel, data in snapshot.items():
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    result.setdefault("warnings", []).append({
        "code": "restored_previous_tts_outputs_after_failed_force_rerun",
        "message": "Previous SessionOutput TTS candidates were restored because the forced Builder-G rerun did not complete.",
    })


def item_start(item: dict[str, Any]) -> float:
    try:
        return float(item.get("start") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def item_end(item: dict[str, Any]) -> float:
    try:
        end = float(item.get("end") or 0.0)
    except (TypeError, ValueError):
        end = 0.0
    return max(end, item_start(item))


def dialogue(item: dict[str, Any]) -> str:
    return str(item.get("dialogue") or "").strip()


def text_info_score(text: str) -> float:
    clean = "".join(ch for ch in text if not ch.isspace())
    if not clean:
        return 0.0
    weak = {"啊", "嗯", "呃", "然后", "就是", "这个", "那个", "所以"}
    weak_hits = sum(clean.count(token) for token in weak)
    cjk = sum(1 for ch in clean if "\u4e00" <= ch <= "\u9fff")
    product_hits = sum(clean.count(token) for token in ("买", "产品", "品牌", "润喉", "蜂胶", "效果", "推荐", "每天", "家", "老公"))
    return max(0.0, min(1.0, (cjk / 18.0) + product_hits * 0.08 - weak_hits * 0.03))


def image_quality_score(path: Path) -> float:
    if cv2 is None or not path.exists():
        return 0.5 if path.exists() else 0.0
    image = cv2.imread(str(path))
    if image is None:
        return 0.25
    height, width = image.shape[:2]
    size_score = min(1.0, (width * height) / (480 * 720))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    blur_score = min(1.0, blur / 200.0)
    brightness = float(gray.mean()) / 255.0
    brightness_score = 1.0 - min(1.0, abs(brightness - 0.52) / 0.52)
    return max(0.0, min(1.0, 0.45 * size_score + 0.35 * blur_score + 0.20 * brightness_score))


def average_hash(path: Path) -> str:
    if cv2 is None or not path.exists():
        return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return hashlib.sha1(path.read_bytes() if path.exists() else str(path).encode("utf-8")).hexdigest()[:16]
    small = cv2.resize(image, (8, 8))
    mean = float(small.mean())
    bits = ["1" if value > mean else "0" for value in small.flatten()]
    return f"{int(''.join(bits), 2):016x}"


def hamming(left: str, right: str) -> int:
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except Exception:
        return 64


def choose_sample_window(items: list[dict[str, Any]], target_duration: float) -> dict[str, Any]:
    ordered = sorted(items, key=item_start)
    best: dict[str, Any] | None = None
    for start_item in ordered:
        start = item_start(start_item)
        end = start + target_duration
        window_items = [item for item in ordered if item_end(item) > start and item_start(item) < end]
        if not window_items:
            continue
        coverage = min(target_duration, max(item_end(item) for item in window_items) - start)
        info = sum(text_info_score(dialogue(item)) for item in window_items)
        score = coverage / max(0.1, target_duration) + info * 0.35 + min(1.0, len(window_items) / 5.0) * 0.25
        candidate = {"start": round(start, 3), "end": round(end, 3), "duration": round(target_duration, 3), "items": window_items, "score": round(score, 4)}
        if best is None or score > float(best["score"]):
            best = candidate
    if best:
        return best
    first = item_start(ordered[0])
    return {"start": first, "end": round(first + target_duration, 3), "duration": round(target_duration, 3), "items": ordered[:], "score": 0.0}


def forced_sample_window(items: list[dict[str, Any]], start: float, duration: float) -> dict[str, Any]:
    ordered = sorted(items, key=item_start)
    safe_start = max(0.0, float(start or 0.0))
    safe_duration = max(0.1, float(duration or 0.0))
    end = safe_start + safe_duration
    window_items = [item for item in ordered if item_end(item) > safe_start and item_start(item) < end]
    return {
        "start": round(safe_start, 3),
        "end": round(end, 3),
        "duration": round(safe_duration, 3),
        "items": window_items or ordered[:],
        "score": 1.0,
        "source": "manual_reference_range",
    }


def frame_path(workspace: Path, item: dict[str, Any]) -> Path:
    raw = str(item.get("image_path") or "").strip()
    path = Path(raw)
    return path if path.is_absolute() else workspace / path


def representative_frame_score(workspace: Path, item: dict[str, Any], window: dict[str, Any]) -> dict[str, Any]:
    start = float(window["start"])
    end = float(window["end"])
    mid = (start + end) / 2.0
    t = (item_start(item) + item_end(item)) / 2.0
    span = max(0.1, end - start)
    timeline = 1.0 - min(1.0, abs(t - mid) / span)
    duration = max(0.0, item_end(item) - item_start(item))
    duration_score = min(1.0, duration / 2.0) if duration <= 4.0 else max(0.2, 1.0 - (duration - 4.0) / 6.0)
    path = frame_path(workspace, item)
    info = text_info_score(dialogue(item))
    quality = image_quality_score(path)
    score = 0.30 * timeline + 0.25 * info + 0.15 * duration_score + 0.10 * quality
    return {
        "srt_id": str(item.get("srt_id") or ""),
        "time": round(t, 3),
        "path": relpath(path, workspace),
        "dialogue": dialogue(item),
        "score": round(score, 5),
        "timeline_coverage_score": round(timeline, 5),
        "dialogue_information_score": round(info, 5),
        "duration_score": round(duration_score, 5),
        "image_quality_score": round(quality, 5),
        "hash": average_hash(path),
        "item": item,
    }


def select_representative_frames(workspace: Path, items: list[dict[str, Any]], window: dict[str, Any], max_frames: int) -> list[dict[str, Any]]:
    in_window = [item for item in items if item_end(item) > float(window["start"]) and item_start(item) < float(window["end"])]
    candidates = [representative_frame_score(workspace, item, window) for item in (in_window or items)]
    candidates = sorted(candidates, key=lambda item: float(item["score"]), reverse=True)
    selected: list[dict[str, Any]] = []
    buckets = [(0.0, 0.34), (0.34, 0.67), (0.67, 1.01)]
    span = max(0.1, float(window["end"]) - float(window["start"]))
    for left, right in buckets:
        bucket = [
            item for item in candidates
            if left <= ((float(item["time"]) - float(window["start"])) / span) < right
        ]
        if bucket:
            selected.append(bucket[0])
    for candidate in candidates:
        if len(selected) >= max_frames:
            break
        if any(candidate["srt_id"] == item["srt_id"] for item in selected):
            continue
        if selected and max(hamming(str(candidate["hash"]), str(item["hash"])) for item in selected) < 8:
            continue
        selected.append(candidate)
    selected = sorted(selected[:max_frames], key=lambda item: float(item["time"]))
    return [{key: value for key, value in item.items() if key != "item"} for item in selected]


def build_contact_sheet(workspace: Path, frames: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if cv2 is None:
        output_path.write_bytes(b"")
        return
    cell_w, cell_h = 360, 520
    cols = 3
    rows = max(1, math.ceil(len(frames) / cols))
    sheet = 255 * __import__("numpy").ones((rows * cell_h, cols * cell_w, 3), dtype="uint8")  # type: ignore
    for idx, frame in enumerate(frames):
        row, col = divmod(idx, cols)
        x, y = col * cell_w, row * cell_h
        image = cv2.imread(str(workspace / str(frame["path"])))
        if image is None:
            image = 245 * __import__("numpy").ones((420, 320, 3), dtype="uint8")  # type: ignore
        h, w = image.shape[:2]
        scale = min(cell_w / max(1, w), 430 / max(1, h))
        resized = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))))
        rh, rw = resized.shape[:2]
        ox = x + (cell_w - rw) // 2
        sheet[y : y + rh, ox : ox + rw] = resized[: min(rh, sheet.shape[0] - y), : min(rw, sheet.shape[1] - ox)]
        label = f"{frame.get('srt_id','')} {frame.get('time','')}s"
        cv2.putText(sheet, label[:38], (x + 10, y + 455), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (20, 35, 55), 2)
        cv2.putText(sheet, str(frame.get("dialogue") or "")[:30], (x + 10, y + 485), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (20, 35, 55), 1)
    cv2.imwrite(str(output_path), sheet)


def selected_dialogue(items: list[dict[str, Any]]) -> str:
    return "".join(dialogue(item) for item in items if dialogue(item)).strip()


def srt_timestamp(seconds: float) -> str:
    clean = max(0.0, float(seconds or 0.0))
    hours = int(clean // 3600)
    minutes = int((clean % 3600) // 60)
    whole = int(clean % 60)
    millis = int(round((clean - int(clean)) * 1000))
    if millis >= 1000:
        whole += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"


def selected_srt(items: list[dict[str, Any]], window_start: float) -> str:
    lines: list[str] = []
    for index, item in enumerate(items, 1):
        lines.extend([
            str(index),
            f"{srt_timestamp(item_start(item) - window_start)} --> {srt_timestamp(item_end(item) - window_start)}",
            dialogue(item),
            "",
        ])
    return "\n".join(lines).strip()


def build_scene_prompt(window: dict[str, Any], frames: list[dict[str, Any]]) -> str:
    items = window.get("items") if isinstance(window.get("items"), list) else []
    dialogue_text = selected_dialogue(items)
    srt_text = selected_srt(items, float(window["start"]))
    frame_rows = "\n".join(f"- {frame['srt_id']} @ {frame['time']}s: {frame['dialogue']}" for frame in frames)
    return f"""# Scene Profile Prompt

## Task
请根据 contact sheet 和下方对白信息，判断这个 TTS 样本对应的声音场景。

## Input Images
你会看到一张 contact sheet。每格图片上已经标注 srt_id、时间和对白摘要。

## Selected Frames
{frame_rows}

## Selected Dialogue
{dialogue_text}

## Selected SRT
{srt_text}

## Required Output JSON
{{
  "scene_type": "",
  "speaker_profile": "",
  "environment": "",
  "emotion": "",
  "delivery_style": "",
  "pace": "",
  "avoid": [],
  "voice_prompt_guidance": {{
    "speaker": "",
    "scene": "",
    "delivery": "",
    "emotion": "",
    "pace": "",
    "recording_style": "",
    "naturalness": "",
    "performance_risk": "",
    "avoid": []
  }},
  "visual_evidence": [],
  "dialogue_evidence": []
}}

## Rules
只输出 JSON，不要输出解释。
不要生成 TTS。
不要推荐具体 voice。
`voice_prompt_guidance` 是给后续 Gemini TTS 使用的声音指导，必须基于画面和对白证据。
如果是产品、讲解、推荐、销售或 KOL 场景，也要判断它在声音上更像“自然自拍视频口播”还是“正式广告/直播叫卖”；不要因为出现产品或推荐内容就自动写成夸张、热情、强推。
`voice_prompt_guidance` 里的 wording 要适合直接写入 TTS prompt，优先使用自然中文描述，避免只输出 marketing、KOL、promotion、enthusiastic、persuasive 这类容易导致过度表演的标签，除非证据明确需要这种风格。
`performance_risk` 写出最需要避免的过度表达风险，例如广告腔、直播叫卖感、夸张重音、尾音上扬过多、过甜、过嗲、过兴奋。
如果证据不足，用 conservative_unknown，并在 evidence 中说明不足。
"""


def rule_scene_profile(window: dict[str, Any], frames: list[dict[str, Any]]) -> dict[str, Any]:
    text = selected_dialogue(window.get("items") or [])
    is_product = any(token in text for token in ("买", "产品", "品牌", "润喉", "蜂胶", "推荐", "效果"))
    is_home = any(token in text for token in ("老公", "每天", "家", "早上"))
    return {
        "scene_type": "home_lifestyle_product_recommendation" if is_product else "short_video_spoken_dialogue",
        "speaker_profile": "young adult female lifestyle sharer",
        "environment": "indoor home/kitchen" if is_home or is_product else "unknown short-video scene",
        "emotion": "friendly, slightly lively, persuasive" if is_product else "natural, conversational",
        "delivery_style": "natural close-mic short-video product sharing" if is_product else "natural close-mic short-video narration",
        "pace": "slightly fast",
        "avoid": ["broadcast tone", "overly sweet", "dialect accent", "foreign accent"],
        "voice_prompt_guidance": {
            "speaker": "普通中文短视频口播者",
            "scene": "自然生活分享" if is_product else "自然短视频口播",
            "delivery": "像日常自拍视频里自然说话，清楚但不表演",
            "emotion": "自然、轻松、克制",
            "pace": "自然偏快但不赶",
            "recording_style": "近距离、真实、干净",
            "naturalness": "high",
            "performance_risk": "避免广告腔、叫卖感、夸张重音和过度兴奋",
            "avoid": ["不要播音腔", "不要广告腔", "不要直播叫卖感", "不要夸张重音", "不要尾音上扬太多"],
        },
        "visual_evidence": [str(frame.get("srt_id") or "") for frame in frames],
        "dialogue_evidence": [text[:120]],
        "source": "rule_fallback",
    }


def post_json(url: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ToolError(f"HTTP {exc.code}: {detail}") from exc


def parse_json_from_text(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.strip("`")
        clean = clean.replace("json\n", "", 1).strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end > start:
        clean = clean[start : end + 1]
    payload = json.loads(clean)
    return payload if isinstance(payload, dict) else {}


def extract_inline_audio(response: dict[str, Any], output_path: Path) -> dict[str, Any] | None:
    for candidate in response.get("candidates") or []:
        content = candidate.get("content") if isinstance(candidate, dict) else {}
        for part in content.get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") if isinstance(part, dict) else {}
            if not isinstance(inline, dict):
                continue
            encoded = str(inline.get("data") or "")
            if not encoded:
                continue
            mime_type = str(inline.get("mimeType") or inline.get("mime_type") or "audio/wav")
            raw = base64.b64decode(encoded)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(wav_from_pcm(raw) if "pcm" in mime_type or "l16" in mime_type else raw)
            return {"mime_type": mime_type, "duration": media_duration(output_path)}
    return None


def gemini_tts_finish_summary(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for candidate in response.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        reason = str(candidate.get("finishReason") or "").strip()
        message = str(candidate.get("finishMessage") or "").strip()
        if reason or message:
            parts.append(": ".join(item for item in [reason, message] if item))
    return "; ".join(parts)


def extract_tts_body(prompt_text: str) -> str:
    text = str(prompt_text or "").strip()
    for marker in ("正文：", "正文:"):
        index = text.rfind(marker)
        if index >= 0:
            return text[index + len(marker):].strip()
    return text


def write_gemini_tts_retry_prompt(prompt_path: Path, prompt_text: str) -> Path:
    body = extract_tts_body(prompt_text)
    retry_path = prompt_path.with_name(f"{prompt_path.stem}_retry_plain{prompt_path.suffix}")
    retry_text = (
        "请用自然普通话朗读以下文本。只朗读文本内容，不要读出任何说明、标题或标点名称。\n\n"
        f"{body}"
    ).strip()
    retry_path.write_text(retry_text, encoding="utf-8")
    return retry_path


def write_gemini_tts_body_only_retry_prompt(prompt_path: Path, prompt_text: str) -> Path:
    body = extract_tts_body(prompt_text)
    retry_path = prompt_path.with_name(f"{prompt_path.stem}_retry_body_only{prompt_path.suffix}")
    retry_path.write_text(body, encoding="utf-8")
    return retry_path


def is_gemini_invalid_argument_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "http 400" in message and "invalid_argument" in message


def gemini_tts_payload(prompt_text: str, voice: str) -> dict[str, Any]:
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    return payload


def call_gemini_tts(api_key: str, model: str, voice: str, prompt_path: Path, output_path: Path) -> dict[str, Any]:
    prompt_text = prompt_path.read_text(encoding="utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
    try:
        response = post_json(url, gemini_tts_payload(prompt_text, voice), timeout=180)
    except ToolError as exc:
        if not is_gemini_invalid_argument_error(exc):
            raise
        retry_path = write_gemini_tts_body_only_retry_prompt(prompt_path, prompt_text)
        retry_response = post_json(url, gemini_tts_payload(retry_path.read_text(encoding="utf-8"), voice), timeout=180)
        retry_audio_meta = extract_inline_audio(retry_response, output_path)
        if retry_audio_meta is not None:
            return {
                **retry_audio_meta,
                "prompt_path": str(retry_path),
                "retry_used": True,
                "retry_reason": "primary_invalid_argument",
            }
        raise ToolError(
            "Gemini TTS invalid argument and body-only retry did not return audio: "
            f"primary={str(exc)[:500]} retry={json.dumps(retry_response, ensure_ascii=False)[:900]}"
        ) from exc
    audio_meta = extract_inline_audio(response, output_path)
    if audio_meta is not None:
        return {**audio_meta, "prompt_path": str(prompt_path), "retry_used": False}

    retry_path = write_gemini_tts_retry_prompt(prompt_path, prompt_text)
    retry_text = retry_path.read_text(encoding="utf-8")
    retry_response = post_json(url, gemini_tts_payload(retry_text, voice), timeout=180)
    retry_audio_meta = extract_inline_audio(retry_response, output_path)
    if retry_audio_meta is not None:
        return {
            **retry_audio_meta,
            "prompt_path": str(retry_path),
            "retry_used": True,
            "retry_reason": gemini_tts_finish_summary(response) or "primary_response_without_audio",
        }
    raise ToolError(
        "Gemini TTS did not return audio after retry: "
        f"primary={json.dumps(response, ensure_ascii=False)[:900]} "
        f"retry={json.dumps(retry_response, ensure_ascii=False)[:900]}"
    )


def read_wav_features(path: Path) -> dict[str, float]:
    duration = media_duration(path)
    try:
        with wave.open(str(path), "rb") as reader:
            frames = reader.readframes(reader.getnframes())
            width = reader.getsampwidth()
            channels = reader.getnchannels()
            if width != 2:
                return {"duration": duration, "rms": 0.0, "zero_crossing": 0.0, "energy": 0.0}
            samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
            if channels > 1:
                samples = samples[::channels]
            if not samples:
                return {"duration": duration, "rms": 0.0, "zero_crossing": 0.0, "energy": 0.0}
            norm = [sample / 32768.0 for sample in samples]
            rms = math.sqrt(sum(value * value for value in norm) / len(norm))
            crossings = sum(1 for left, right in zip(norm, norm[1:]) if (left < 0 <= right) or (left >= 0 > right))
            zcr = crossings / max(1, len(norm) - 1)
            return {"duration": duration, "rms": rms, "zero_crossing": zcr, "energy": min(1.0, rms / 0.18)}
    except Exception:
        return {"duration": duration, "rms": 0.0, "zero_crossing": 0.0, "energy": 0.0}


def exp_similarity(left: float, right: float, scale: float) -> float:
    return math.exp(-abs(left - right) / max(1e-6, scale))


def score_audio(reference: dict[str, float], candidate: dict[str, float], target_duration: float, scene_fit: float) -> tuple[float, dict[str, float]]:
    duration = exp_similarity(target_duration, float(candidate.get("duration") or 0.0), 1.2)
    energy = exp_similarity(float(reference.get("rms") or 0.0), float(candidate.get("rms") or 0.0), 0.12)
    zcr = exp_similarity(float(reference.get("zero_crossing") or 0.0), float(candidate.get("zero_crossing") or 0.0), 0.08)
    clarity = min(1.0, float(candidate.get("energy") or 0.0) + 0.25)
    score = 0.30 * energy + 0.22 * zcr + 0.18 * duration + 0.18 * scene_fit + 0.12 * clarity
    return score, {"energy": energy, "zero_crossing": zcr, "duration": duration, "scene_fit": scene_fit, "clarity": clarity}


def voice_role(voice: str) -> str:
    return VOICE_TEST_ROLES.get(voice, "测试该 voice 是否适合 scene_profile 中的说话人、场景和语速")


def scene_guidance(scene_profile: dict[str, Any]) -> dict[str, Any]:
    guidance = scene_profile.get("voice_prompt_guidance")
    return guidance if isinstance(guidance, dict) else {}


def scene_text(scene_profile: dict[str, Any], guidance: dict[str, Any], guidance_key: str, profile_key: str, default: str) -> str:
    return str(guidance.get(guidance_key) or scene_profile.get(profile_key) or default).strip()


def avoid_lines(scene_profile: dict[str, Any]) -> list[str]:
    guidance = scene_guidance(scene_profile)
    guidance_avoid = guidance.get("avoid") if isinstance(guidance.get("avoid"), list) else []
    values = [str(item) for item in guidance_avoid if str(item).strip()]
    if values:
        return values[:5]
    avoid = scene_profile.get("avoid") if isinstance(scene_profile.get("avoid"), list) else []
    defaults = ["不要播音腔", "不要广告腔", "不要直播叫卖感", "不要夸张重音", "不要过度甜或过度表演"]
    values = [str(item) for item in avoid if str(item).strip()]
    return values[:5] or defaults


def build_voice_prompt(scene_profile: dict[str, Any], voice: str, sample_text: str, variant: str = "base", tempo_hint: str = "") -> str:
    guidance = scene_guidance(scene_profile)
    avoid = avoid_lines(scene_profile)
    style_extra = {
        "closest_reference": "贴近原片的自然说话方式和节奏，清楚但不要演。",
        "natural_selfie": "更像真实手机自拍视频里的自然分享，语气轻松、克制。",
        "calm_clear": "更清楚一点，但保持日常说话感，不要变成讲解腔或广告腔。",
        "tempo_faster": "语速略快一点，停顿更短，但不要急促或兴奋。",
        "tempo_slower": "语速略慢一点，保留自然停顿，但不要拖腔。",
        "base": "保持自然口播，不主动加强情绪。",
    }.get(variant, "")
    if tempo_hint:
        style_extra = f"{style_extra} {tempo_hint}".strip()
    speaker = scene_text(scene_profile, guidance, "speaker", "speaker_profile", "中文短视频口播者")
    scene = scene_text(scene_profile, guidance, "scene", "scene_type", "短视频口播")
    environment = str(scene_profile.get("environment") or "").strip()
    delivery = scene_text(scene_profile, guidance, "delivery", "delivery_style", "自然近距离口播")
    emotion = scene_text(scene_profile, guidance, "emotion", "emotion", "自然、轻松、克制")
    pace = scene_text(scene_profile, guidance, "pace", "pace", "自然")
    recording_style = str(guidance.get("recording_style") or "近距离、真实、干净").strip()
    naturalness = str(guidance.get("naturalness") or "尽量像真实日常说话").strip()
    performance_risk = str(guidance.get("performance_risk") or "避免广告腔、叫卖感、夸张重音、尾音上扬太多").strip()
    return f"""请用普通话朗读下面正文，只朗读正文，不要读出任何说明。

声音方向：
- 说话人：{speaker}
- 场景：{scene}{f"，{environment}" if environment else ""}
- 表达方式：{delivery}
- 情绪：{emotion}
- 语速：{pace}
- 收音感：{recording_style}
- 自然度：{naturalness}

当前 voice 适配方向：
- 使用 voice: {voice}
- 该 voice 在本轮用于测试：{voice_role(voice)}；无论 voice 本身多有表现力，都要服从 scene_profile 的自然口播判断。

风格变体：
- {variant}
- {style_extra}

避免：
- {avoid[0] if len(avoid) > 0 else '不要播音腔'}
- {avoid[1] if len(avoid) > 1 else '不要广告腔'}
- {avoid[2] if len(avoid) > 2 else '不要直播叫卖感'}
- {avoid[3] if len(avoid) > 3 else '不要夸张重音'}
- {avoid[4] if len(avoid) > 4 else '不要尾音上扬太多'}
- {performance_risk}

正文：
{sample_text}
"""


def fit_audio_to_duration(input_audio: Path, output_audio: Path, target_duration: float) -> dict[str, float]:
    raw_duration = media_duration(input_audio) or target_duration
    tempo = raw_duration / target_duration if target_duration > 0 else 1.0
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    try:
        filters = f"aresample=48000,aformat=channel_layouts=stereo,atempo={max(0.5, min(2.0, tempo)):.6f},apad=pad_dur={target_duration:.6f},atrim=duration={target_duration:.6f},asetpts=N/SR/TB"
        run_cmd([find_binary("ffmpeg"), "-y", "-i", str(input_audio), "-af", filters, "-ar", "48000", "-ac", "2", str(output_audio)], timeout=180)
    except Exception:
        shutil.copyfile(input_audio, output_audio)
    return {"raw_duration": raw_duration, "target_duration": target_duration, "tempo": tempo, "fit_duration": media_duration(output_audio) or target_duration}


def session_candidate_path(rank: int) -> str:
    return f"{SESSION_TTS_DIR_REL}/tts_builder_candidate_{rank:03d}.wav"


def generate_candidate(api_key: str, model: str, voice: str, prompt_path: Path, raw_path: Path, reference_features: dict[str, float], target_duration: float, scene_fit: float, force: bool) -> dict[str, Any]:
    if raw_path.exists() and raw_path.stat().st_size > 0 and not force:
        gemini_meta = {"duration": media_duration(raw_path), "cached": True}
    else:
        gemini_meta = call_gemini_tts(api_key, model, voice, prompt_path, raw_path)
    features = read_wav_features(raw_path)
    score, parts = score_audio(reference_features, features, target_duration, scene_fit)
    raw_duration = media_duration(raw_path)
    tempo = raw_duration / target_duration if target_duration > 0 else 1.0
    return {
        "provider": "google",
        "model": model,
        "voice": voice,
        "prompt_path": "",
        "raw_audio": "",
        "gemini_meta": gemini_meta,
        "features": features,
        "score": round(score, 6),
        "score_parts": {key: round(float(value), 6) for key, value in parts.items()},
        "raw_duration": round(raw_duration, 3),
        "target_duration": round(target_duration, 3),
        "tempo": round(tempo, 6),
        "tempo_source": "measured_after_raw_tts_generation",
        "needs_review": tempo > 1.20 or tempo < 0.85,
    }


def prompt_file_payload(workspace: Path, row: dict[str, Any]) -> dict[str, str]:
    prompt_abs_raw = str(row.get("prompt_abs_path") or "").strip()
    prompt_abs = Path(prompt_abs_raw) if prompt_abs_raw else None
    prompt_rel = str(row.get("prompt_path") or "")
    prompt_text = ""
    if prompt_abs and prompt_abs.is_file():
        prompt_text = prompt_abs.read_text(encoding="utf-8")
    elif prompt_rel:
        prompt_path = workspace / prompt_rel
        if prompt_path.is_file():
            prompt_text = prompt_path.read_text(encoding="utf-8")
    return {
        "prompt": prompt_text,
        "generation_prompt": prompt_text,
        "tts_builder_prompt": prompt_text,
        "prompt_path": prompt_rel,
        "prompt_source": "Prompt",
        "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest() if prompt_text else "",
    }


def build_final_payload(workspace: Path, window: dict[str, Any], scene_profile: dict[str, Any], final_rows: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for rank, row in enumerate(final_rows, 1):
        prompt_payload = prompt_file_payload(workspace, row)
        items.append({
            "rank": rank,
            "candidate_id": f"tts_{rank:03d}",
            "provider": row.get("provider"),
            "model": row.get("model"),
            "voice": row.get("voice"),
            "voice_label": row.get("voice"),
            "selected": rank == 1,
            **prompt_payload,
            "sample_audio_path": session_candidate_path(rank),
            "tempo": row.get("tempo"),
            "tempo_source": row.get("tempo_source"),
            "raw_duration": row.get("raw_duration"),
            "target_duration": row.get("target_duration"),
            "fit_duration": row.get("fit_duration"),
            "needs_review": row.get("needs_review", False),
            "score": row.get("score"),
            "reason": row.get("reason") or "Builder-G candidate ranked by measured audio fit, scene fit, and tempo stability.",
        })
    selected = items[0] if items else {}
    return {
        "schema_version": "analysis_v1_tts_builder_g_candidates_0.1",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "sample_policy": {
            "selected_duration": window.get("duration"),
            "tested_durations": [4, 8, 16],
            "selected_range": {"start": window.get("start"), "end": window.get("end")},
            "reason": "16s is used for final Builder-G candidate ranking; 8s is used for voice screening.",
        },
        "scene_profile": scene_profile,
        "selected_candidate_id": selected.get("candidate_id", ""),
        "selected_candidate": selected,
        "selected_generation_prompt": selected.get("generation_prompt", ""),
        "selected_tts_builder_prompt": selected.get("tts_builder_prompt", ""),
        "selected_prompt_path": selected.get("prompt_path", ""),
        "selected_prompt_sha256": selected.get("prompt_sha256", ""),
        "candidates": items,
        "created_at": now_iso(),
    }


def select_final_rows(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda item: (not bool(item.get("needs_review")), float(item.get("score") or 0.0)), reverse=True)
    selected: list[dict[str, Any]] = []
    seen_voices: set[str] = set()
    for row in ranked:
        voice = str(row.get("voice") or "").strip()
        if not voice or voice in seen_voices:
            continue
        selected.append(row)
        seen_voices.add(voice)
        if len(selected) >= count:
            return selected
    for row in ranked:
        if row in selected:
            continue
        selected.append(row)
        if len(selected) >= count:
            break
    return selected


def run_builder(workspace: Path, args: Args, variables: dict[str, Any], final_items_payload: dict[str, Any], reference_audio: Path, result: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs(workspace)
    runtime_config = resolve_runtime_config(args, variables)
    provider = runtime_config["provider"]
    tts_model = runtime_config["tts_model"]
    scene_run_config: RunModelConfig | None = None
    items = [item for item in final_items_payload.get("items", []) if isinstance(item, dict) and dialogue(item)]
    target_duration = float(args.target_duration)
    quick_duration = float(args.quick_duration)
    if float(args.reference_duration or 0.0) > 0:
        target_duration = float(args.reference_duration)
        quick_duration = min(float(args.quick_duration), target_duration)
        window = forced_sample_window(items, float(args.reference_start or 0.0), target_duration)
        quick_window = forced_sample_window(items, float(args.reference_start or 0.0), quick_duration)
    else:
        window = choose_sample_window(items, target_duration)
        quick_window = choose_sample_window(items, quick_duration)
    frames = select_representative_frames(workspace, items, window, int(args.max_scene_frames))
    contact_sheet = workspace / WORKING_CONTACT_SHEET_REL
    build_contact_sheet(workspace, frames, contact_sheet)

    scene_prompt_path = workspace / SCENE_PROFILE_PROMPT_REL
    write_text_if_needed(scene_prompt_path, build_scene_prompt(window, frames), args.force_regenerate_prompts or args.force)
    scene_profile: dict[str, Any]
    scene_model_calls = 0
    if args.scene_profile_mode == "rule":
        scene_profile = rule_scene_profile(window, frames)
    else:
        try:
            scene_run_config = resolve_scene_profile_run_model_config(variables)
            scene_profile = call_scene_profile_run_model(args, variables, scene_run_config, scene_prompt_path, contact_sheet, workspace)
            scene_profile["source"] = "single_opencode_run_model_call"
            scene_profile["model"] = {"provider": scene_run_config.provider, "model": scene_run_config.model, "source": scene_run_config.source}
            scene_model_calls = 1
        except Exception as exc:
            if args.scene_profile_mode == "model":
                raise
            result["warnings"].append({"code": "scene_profile_model_fallback", "message": f"Scene profile model call failed; used rule fallback. {exc}"})
            scene_profile = rule_scene_profile(window, frames)
    write_json(workspace / OUTPUT_SCENE_PROFILE_REL, scene_profile)

    api_key = load_google_api_key(args, kind="tts", provider=provider, model=tts_model)
    voices = [item.strip() for item in args.voices.split(",") if item.strip()] or DEFAULT_VOICES
    sample_text_quick = selected_dialogue(quick_window.get("items") or []) or selected_dialogue(window.get("items") or [])
    sample_text_final = selected_dialogue(window.get("items") or [])
    reference_features = read_wav_features(reference_audio)
    round1_rows: list[dict[str, Any]] = []
    for index, voice in enumerate(voices, 1):
        prompt_rel = f"{PROMPT_DIR_REL}/round1_voice_{index:03d}_prompt.txt"
        prompt_path = workspace / prompt_rel
        write_text_if_needed(prompt_path, build_voice_prompt(scene_profile, voice, sample_text_quick, "base"), args.force_regenerate_prompts or args.force)
        raw_path = workspace / WORKING_RAW_DIR_REL / f"round1_voice_{index:03d}_{voice}.wav"
        row = generate_candidate(api_key, tts_model, voice, prompt_path, raw_path, reference_features, quick_duration, 0.65, args.force)
        row.update({"stage": "round1_voice_screen", "voice": voice, "prompt_path": prompt_rel, "prompt_abs_path": str(prompt_path), "raw_audio": relpath(raw_path, workspace)})
        round1_rows.append(row)
    top_voice_rows = sorted(round1_rows, key=lambda item: float(item.get("score") or 0.0), reverse=True)[: max(1, int(args.top_voices))]

    round2_rows: list[dict[str, Any]] = []
    variants = ["closest_reference", "natural_selfie"]
    candidate_index = 0
    for parent in top_voice_rows:
        voice = str(parent.get("voice") or "")
        for variant in variants:
            candidate_index += 1
            prompt_rel = f"{PROMPT_DIR_REL}/round2_candidate_{candidate_index:03d}_prompt.txt"
            prompt_path = workspace / prompt_rel
            write_text_if_needed(prompt_path, build_voice_prompt(scene_profile, voice, sample_text_final, variant), args.force_regenerate_prompts or args.force)
            raw_path = workspace / WORKING_RAW_DIR_REL / f"round2_candidate_{candidate_index:03d}_{voice}_{variant}.wav"
            row = generate_candidate(api_key, tts_model, voice, prompt_path, raw_path, reference_features, target_duration, 0.85, args.force)
            row.update({"stage": "round2_formal_candidate", "variant": variant, "parent_score": parent.get("score"), "prompt_path": prompt_rel, "prompt_abs_path": str(prompt_path), "raw_audio": relpath(raw_path, workspace)})
            round2_rows.append(row)

    ranked = sorted(round2_rows, key=lambda item: float(item.get("score") or 0.0), reverse=True)
    adjusted_rows: list[dict[str, Any]] = []
    for row in ranked[: max(3, int(args.final_count) * 3)]:
        tempo = float(row.get("tempo") or 1.0)
        candidate_id = str(row.get("raw_audio") or "candidate").split("/")[-1].rsplit(".", 1)[0]
        if 1.10 < tempo <= 1.20 or 0.85 <= tempo < 0.92:
            hint = "语速更快一点，停顿更短" if tempo > 1.0 else "语速稍慢一点，保留自然停顿"
            prompt_rel = f"{PROMPT_DIR_REL}/round3_{candidate_id}_tempo_fix_prompt.txt"
            prompt_path = workspace / prompt_rel
            write_text_if_needed(prompt_path, build_voice_prompt(scene_profile, str(row.get("voice") or ""), sample_text_final, "tempo_faster" if tempo > 1.0 else "tempo_slower", hint), args.force_regenerate_prompts or args.force)
            raw_path = workspace / WORKING_RAW_DIR_REL / f"round3_{candidate_id}_tempo_fix.wav"
            fixed = generate_candidate(api_key, tts_model, str(row.get("voice") or ""), prompt_path, raw_path, reference_features, target_duration, 0.88, args.force)
            fixed.update({**row, **fixed, "stage": "round3_tempo_fix", "prompt_path": prompt_rel, "prompt_abs_path": str(prompt_path), "raw_audio": relpath(raw_path, workspace), "before_tempo": tempo})
            row = fixed
        fit_path = workspace / WORKING_FIT_DIR_REL / f"{candidate_id}_fit.wav"
        fit_meta = fit_audio_to_duration(workspace / str(row["raw_audio"]), fit_path, target_duration)
        row = {**row, "fit_audio": relpath(fit_path, workspace), "fit_duration": round(float(fit_meta.get("fit_duration") or target_duration), 3), "fit_meta": fit_meta}
        adjusted_rows.append(row)

    final_rows = select_final_rows(adjusted_rows, int(args.final_count))
    for rank, row in enumerate(final_rows, 1):
        shutil.copyfile(workspace / str(row["fit_audio"]), workspace / session_candidate_path(rank))
    final_payload = build_final_payload(workspace, window, scene_profile, final_rows)
    write_json(
        workspace / OUTPUT_VOICE_PLAN_REL,
        {
            "voices": voices,
            "round1_top_voices": top_voice_rows,
            "scene_model_calls": scene_model_calls,
            "runtime_config": runtime_config,
            "scene_profile_model_config": (
                {"provider": scene_run_config.provider, "model": scene_run_config.model, "source": scene_run_config.source}
                if scene_run_config
                else {"source": "rule_mode_or_fallback"}
            ),
        },
    )
    write_json(workspace / OUTPUT_SCORING_AUDIT_REL, {"round1": round1_rows, "round2": round2_rows, "final": final_rows})
    write_json(workspace / OUTPUT_FINAL_REL, final_payload)
    write_json(workspace / SESSION_TTS_FINAL_REL, final_payload)
    state = {
        "tool": TOOL_NAME,
        "status": "completed",
        "phase": "finalize",
        "scene_model_calls": scene_model_calls,
        "outputs": {"tts_builder_candidates": SESSION_TTS_FINAL_REL},
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["status"] = "completed"
    result["inputs"] = {
        "variables": VARIABLES_REL,
        "final_srt_frame_items": SESSION_FINAL_ITEMS_REL,
        "reference_audio": SESSION_AUDIO_REFERENCE_REL,
    }
    result["outputs"] = {
        "tts_builder_candidates": SESSION_TTS_FINAL_REL,
        "candidate_audio_001": session_candidate_path(1),
        "candidate_audio_002": session_candidate_path(2),
        "candidate_audio_003": session_candidate_path(3),
    }
    result["counts"] = {
        "scene_model_calls": scene_model_calls,
        "round1_tts_calls": len(round1_rows),
        "round2_tts_calls": len(round2_rows),
        "final_candidates": len(final_rows),
    }
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_FINAL_ITEMS_REL,
        WORKING_CONTACT_SHEET_REL,
        SCENE_PROFILE_PROMPT_REL,
        OUTPUT_SCENE_PROFILE_REL,
        OUTPUT_VOICE_PLAN_REL,
        OUTPUT_SCORING_AUDIT_REL,
        OUTPUT_FINAL_REL,
        SESSION_TTS_FINAL_REL,
        session_candidate_path(1),
        session_candidate_path(2),
        session_candidate_path(3),
        REPORT_RESULT_REL,
    ]
    return final_payload


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    session_tts_snapshot: dict[str, bytes] = {}
    try:
        validate_workspace(workspace)
        if args.force:
            session_tts_snapshot = snapshot_session_tts_outputs(workspace)
            force_reset(workspace, result)
        ensure_dirs(workspace)
        for rel in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", PROMPT_DIR_REL, f"{TOOL_DIR_NAME}/Report", SESSION_TTS_DIR_REL):
            result["prepared_directories"].append(rel)
        variables = load_variables(workspace)
        final_items = load_final_items(workspace)
        reference_audio = resolve_reference_audio(workspace, args)
        write_json(workspace / WORKING_VARIABLES_REL, variables)
        write_json(workspace / WORKING_FINAL_ITEMS_REL, final_items)
        if args.resume and (workspace / SESSION_TTS_FINAL_REL).exists() and not args.force:
            final_payload = read_json(workspace / SESSION_TTS_FINAL_REL)
            result["status"] = "completed"
            result["outputs"] = {"tts_builder_candidates": SESSION_TTS_FINAL_REL}
            result["counts"] = {"final_candidates": len(final_payload.get("candidates") or []), "reused": 1}
            result["warnings"].append({"code": "reused_completed_output", "message": "Existing TTS Builder-G candidates were reused."})
        else:
            run_builder(workspace, args, variables, final_items, reference_audio, result)
    except BlockedError as exc:
        add_block(result, exc.code, exc.message)
    except PermissionError as exc:
        add_block(result, "workspace_permission_denied", f"Cannot read/write Analysis_V1 workspace. Original error: {exc}")
    except Exception as exc:
        result["status"] = "failed"
        result["warnings"].append({"code": "unexpected_error", "message": str(exc)})
    if result.get("status") != "completed" and args.force:
        restore_session_tts_outputs(workspace, session_tts_snapshot, result)
    result["updated_at"] = now_iso()
    result["warnings"].extend(scan_for_sensitive_output(result))
    try:
        if workspace.exists() and workspace.is_dir():
            (workspace / f"{TOOL_DIR_NAME}/Report").mkdir(parents=True, exist_ok=True)
            write_json(workspace / REPORT_RESULT_REL, result)
    except Exception as exc:
        result["warnings"].append({"code": "result_write_failed", "message": str(exc)})
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Build three Builder-G/Gemini TTS voice candidates from final SRT-frame items.")
    parser.add_argument("--workspace", default="", help="Analysis_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--mode", choices=["fast", "balanced", "quality"], default="balanced")
    parser.add_argument("--scene-profile-mode", choices=["auto", "model", "rule"], default="auto")
    parser.add_argument("--tts-model", default="", help="Explicit Gemini TTS model override. Defaults to SessionContext/Variables.json.")
    parser.add_argument("--scene-model", default="", help="Legacy Gemini scene-profile override kept for compatibility; Scene_Profile now uses SessionContext run_model_provider/run_model_id.")
    parser.add_argument("--voices", default=",".join(DEFAULT_VOICES), help="Comma-separated Gemini preset voices to screen.")
    parser.add_argument("--target-duration", type=float, default=16.0)
    parser.add_argument("--quick-duration", type=float, default=8.0)
    parser.add_argument("--reference-start", type=float, default=0.0)
    parser.add_argument("--reference-duration", type=float, default=0.0)
    parser.add_argument("--top-voices", type=int, default=3)
    parser.add_argument("--final-count", type=int, default=3)
    parser.add_argument("--max-scene-frames", type=int, default=6)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force-regenerate-prompts", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        mode=str(ns.mode),
        scene_profile_mode=str(ns.scene_profile_mode),
        tts_model=str(ns.tts_model),
        scene_model=str(ns.scene_model),
        voices=str(ns.voices),
        target_duration=float(ns.target_duration),
        quick_duration=float(ns.quick_duration),
        reference_start=float(ns.reference_start),
        reference_duration=float(ns.reference_duration),
        top_voices=int(ns.top_voices),
        final_count=int(ns.final_count),
        max_scene_frames=int(ns.max_scene_frames),
        database_url=str(ns.database_url or ""),
        database_url_env=str(ns.database_url_env or DEFAULT_DATABASE_URL_ENV),
        force=bool(ns.force),
        resume=bool(ns.resume),
        force_regenerate_prompts=bool(ns.force_regenerate_prompts),
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
    result = run(args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} {result['status']}: {result.get('outputs', {}).get('tts_builder_candidates', '')}")
    return 0 if result["status"] in {"completed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
