from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import os
import re
import shutil
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


TOOL_ID = "12_01_Shot_PlanD_TTSDrivenLipSyncVideoGenerate"
TOOL_NAME = TOOL_ID
TOOL_VERSION = "1.0.0"
TOOL_LEVEL = "shot"
REQUIRES = ["rebuild_shot_plan.json", "final_prompt_package", "scene_tts", "scene_assets", "video_config", "lipsync_config"]
PRODUCES = [
    "Assets/<variant_id>/<shot_id>/plan_d.mp4",
    "Assets/<variant_id>/<shot_id>/plan_d_raw.mp4",
    "Assets/<variant_id>/<shot_id>/plan_d_video_prompt_timed.txt",
    "Assets/<variant_id>/<shot_id>/plan_d_video_prompt_timed.json",
    "Assets/<variant_id>/<shot_id>/reports/plan_d_12_01_tts_driven_lipsync_video_generate.json",
]
SUGGESTED_PREVIOUS_TOOLS = ["05_02_Shot_FinalPromptPackageBuild", "07_01_Shot_PlanA_TTSPromptBuild"]
SUGGESTED_NEXT_TOOLS = ["09_02_ShotPlan_PlanA_Compose"]

DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
CONFIG_TABLE = "tool_media_provider_configs"
DEFAULT_TTS_VOICE = "Cherry"
TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
from opencrew_runtime_secrets import apply_provider_proxy, resolve_secret_value

VIDEO_PROMPT_FIELDS = (
    "positive",
    "character_action",
    "speech_speed",
    "voice_description",
    "camera_motion",
    "scene_consistency",
    "product_consistency",
    "negative",
    "model_notes",
)


class ToolError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "")) or "item"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def rel(workspace: Path, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(str(path))
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def resolve_workspace_path(workspace: Path, path_value: str) -> Path:
    path = Path(str(path_value or "")).expanduser()
    return path if path.is_absolute() else workspace / path


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def resolve_database_url(args: argparse.Namespace | None = None) -> str:
    env_name = str(getattr(args, "database_url_env", "") or DEFAULT_DATABASE_URL_ENV)
    explicit = str(getattr(args, "database_url", "") or "")
    return explicit or os.environ.get(env_name) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL


def postgres_connect(database_url: str) -> Any:
    try:
        import psycopg  # type: ignore
    except Exception as exc:
        raise ToolError("PostgreSQL driver psycopg is not available") from exc
    return psycopg.connect(normalize_database_url(database_url))


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def db_row_to_dict(row: Any, columns: list[str]) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return {columns[index]: row[index] for index in range(min(len(columns), len(row)))}


def load_task_context(args: argparse.Namespace) -> dict[str, Any]:
    task_id = int(getattr(args, "task_id", 0) or 0)
    session_id = int(getattr(args, "session_id", 0) or 0)
    if task_id <= 0 and session_id <= 0:
        return {}
    conn = postgres_connect(resolve_database_url(args))
    try:
        with conn.cursor() as cursor:
            if task_id > 0:
                cursor.execute(
                    """
SELECT t.id, t.session_id, t.analysis_task_id, a.session_id AS analysis_session_id,
       t.source_package_path, t.final_prompt, t.run_model_provider, t.run_model_id,
       s.workspace_dir, s.opencode_session_id, analysis_s.workspace_dir AS analysis_workspace_dir
FROM oc_rebuild_tasks t
JOIN sessions s ON s.id = t.session_id
LEFT JOIN openclip_tasks a ON a.id = t.analysis_task_id
LEFT JOIN sessions analysis_s ON analysis_s.id = a.session_id
WHERE t.id = %s
LIMIT 1
""",
                    (task_id,),
                )
            else:
                cursor.execute(
                    """
SELECT t.id, t.session_id, t.analysis_task_id, a.session_id AS analysis_session_id,
       t.source_package_path, t.final_prompt, t.run_model_provider, t.run_model_id,
       s.workspace_dir, s.opencode_session_id, analysis_s.workspace_dir AS analysis_workspace_dir
FROM oc_rebuild_tasks t
JOIN sessions s ON s.id = t.session_id
LEFT JOIN openclip_tasks a ON a.id = t.analysis_task_id
LEFT JOIN sessions analysis_s ON analysis_s.id = a.session_id
WHERE t.session_id = %s
LIMIT 1
""",
                    (session_id,),
                )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
        if not row:
            target = f"Task #{task_id}" if task_id > 0 else f"Session #{session_id}"
            raise ToolError(f"OC-Rebuild {target} not found")
        data = db_row_to_dict(row, columns)
        resolved_session_id = int(data.get("session_id") or 0)
        if task_id > 0 and session_id > 0 and resolved_session_id != session_id:
            raise ToolError(f"OC-Rebuild Task #{task_id} belongs to Session #{resolved_session_id}, not Session #{session_id}")
        return {
            "task_id": int(data.get("id") or 0),
            "session_id": resolved_session_id,
            "analysis_task_id": int(data.get("analysis_task_id") or 0) or None,
            "analysis_session_id": int(data.get("analysis_session_id") or 0) or None,
            "workspace_dir": decode_text(data.get("workspace_dir")).strip(),
            "analysis_workspace_dir": decode_text(data.get("analysis_workspace_dir")).strip(),
            "source_package_path": decode_text(data.get("source_package_path")).strip() or "source_package.json",
            "final_prompt": decode_text(data.get("final_prompt")).strip(),
            "run_model_provider": decode_text(data.get("run_model_provider")).strip(),
            "run_model_id": decode_text(data.get("run_model_id")).strip(),
            "opencode_session_id": decode_text(data.get("opencode_session_id")).strip(),
        }
    finally:
        conn.close()


def resolve_workspace(args: argparse.Namespace, task_context: dict[str, Any]) -> Path:
    explicit = str(getattr(args, "workspace", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    db_workspace = str(task_context.get("workspace_dir") or "").strip()
    if db_workspace:
        return Path(db_workspace).expanduser().resolve()
    raise ToolError("missing --workspace; pass --workspace or --task-id with a valid rebuild session")


def validate_plan_context(plan: dict[str, Any], task_context: dict[str, Any]) -> None:
    if not task_context:
        return
    plan_task = plan.get("task") if isinstance(plan.get("task"), dict) else {}
    expected_task_id = int(task_context.get("task_id") or 0)
    expected_session_id = int(task_context.get("session_id") or 0)
    plan_task_id = int(plan_task.get("task_id") or 0)
    plan_session_id = int(plan_task.get("session_id") or 0)
    if expected_task_id and plan_task_id and plan_task_id != expected_task_id:
        raise ToolError(f"shot plan belongs to Task #{plan_task_id}, not Task #{expected_task_id}")
    if expected_session_id and plan_session_id and plan_session_id != expected_session_id:
        raise ToolError(f"shot plan belongs to Session #{plan_session_id}, not Session #{expected_session_id}")


def load_active_media_config(database_url: str, kind: str) -> dict[str, str]:
    env_provider = os.environ.get(f"OPENCREW_{kind.upper()}_PROVIDER", "").strip()
    env_model = os.environ.get(f"OPENCREW_{kind.upper()}_MODEL", "").strip()
    env_key = os.environ.get(f"OPENCREW_{kind.upper()}_API_KEY", "").strip()
    if env_provider and env_model and env_key:
        return {"kind": kind, "provider": env_provider, "model": env_model, "api_key": env_key}
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
SELECT provider, model, api_key_ref, api_key_ciphertext
FROM {CONFIG_TABLE}
WHERE kind = %s AND active = TRUE AND enabled = TRUE
LIMIT 1
""",
                (kind,),
            )
            row = cursor.fetchone()
        if not row:
            raise ToolError(f"No active {kind} model is configured")
        provider, model, api_key_ref, legacy_key = [decode_text(item).strip() for item in row]
        api_key = resolve_secret_value(api_key_ref, legacy_key)
        if not provider or not model or not api_key:
            raise ToolError(f"Active {kind} config is incomplete")
        apply_provider_proxy(provider)
        return {"kind": kind, "provider": provider, "model": model, "api_key": api_key}
    finally:
        conn.close()


def load_media_config(database_url: str, kind: str, provider: str = "", model: str = "") -> dict[str, str]:
    provider = str(provider or "").strip()
    model = str(model or "").strip()
    if not provider:
        return load_active_media_config(database_url, kind)
    env_provider = os.environ.get(f"OPENCREW_{kind.upper()}_PROVIDER", "").strip()
    env_model = os.environ.get(f"OPENCREW_{kind.upper()}_MODEL", "").strip()
    env_key = os.environ.get(f"OPENCREW_{kind.upper()}_API_KEY", "").strip()
    if env_key and env_provider == provider and (not model or env_model == model):
        return {"kind": kind, "provider": env_provider, "model": env_model, "api_key": env_key}
    conn = postgres_connect(database_url)
    try:
        query = f"""
SELECT provider, model, api_key_ref, api_key_ciphertext
FROM {CONFIG_TABLE}
WHERE kind = %s AND provider = %s AND enabled = TRUE
"""
        params: list[Any] = [kind, provider]
        if model:
            query += " AND model = %s"
            params.append(model)
        query += " ORDER BY active DESC LIMIT 1"
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
        if not row:
            expected = f"{provider}/{model}" if model else provider
            raise ToolError(f"No enabled {kind} model config found for {expected}")
        row_provider, row_model, api_key_ref, legacy_key = [decode_text(item).strip() for item in row]
        api_key = resolve_secret_value(api_key_ref, legacy_key)
        if not row_provider or not row_model or not api_key:
            raise ToolError(f"{kind} config is incomplete for {row_provider}/{row_model}")
        apply_provider_proxy(row_provider)
        return {"kind": kind, "provider": row_provider, "model": row_model, "api_key": api_key}
    finally:
        conn.close()


def redact_config(config: dict[str, str]) -> dict[str, Any]:
    return {key: (len(value) if key == "api_key" else value) for key, value in config.items()}


def post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ToolError(f"POST {url} failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"POST {url} failed: {exc.reason}") from exc


def post_binary_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> tuple[bytes, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "*/*", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read(), str(res.headers.get("Content-Type") or "application/octet-stream")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ToolError(f"POST {url} failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"POST {url} failed: {exc.reason}") from exc


def get_json_request(url: str, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ToolError(f"GET {url} failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"GET {url} failed: {exc.reason}") from exc


def download_binary(url: str, output_path: Path, headers: dict[str, str] | None = None, timeout: int = 600) -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with output_path.open("wb") as handle:
                    shutil.copyfileobj(res, handle)
            return
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:3000]
            raise ToolError(f"Download failed: HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, ssl.SSLError, ConnectionError, TimeoutError) as exc:
            last_error = exc
            output_path.unlink(missing_ok=True)
            if attempt < 3:
                time.sleep(2 * attempt)
                continue
    raise ToolError(f"Download failed after 3 attempts: {last_error}")


def first_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("url", "video_url", "audio_url", "download_url", "uri"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for key in ("output", "response", "data"):
            value = payload.get(key)
            found = first_url(value)
            if found:
                return found
        for value in payload.values():
            found = first_url(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = first_url(value)
            if found:
                return found
    return ""


def first_audio_data(payload: Any) -> str:
    if isinstance(payload, dict):
        audio = payload.get("audio") if isinstance(payload.get("audio"), dict) else {}
        if isinstance(audio, dict) and isinstance(audio.get("data"), str) and audio.get("data"):
            return str(audio.get("data"))
        if isinstance(payload.get("data"), str) and payload.get("data"):
            return str(payload.get("data"))
        for value in payload.values():
            found = first_audio_data(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = first_audio_data(value)
            if found:
                return found
    return ""


def operation_done(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or payload.get("task_status") or "").upper()
    return bool(payload.get("done")) or status in {"SUCCEEDED", "SUCCESS", "COMPLETED", "SUCCESSFUL", "DONE"}


def operation_failed(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or payload.get("task_status") or "").upper()
    if status in {"FAILED", "FAILURE", "CANCELED", "CANCELLED", "REJECTED"}:
        return json.dumps(payload, ensure_ascii=False)[:1200]
    error = payload.get("error")
    return json.dumps(error, ensure_ascii=False)[:1200] if error else ""


def find_ffmpeg() -> str:
    for candidate in (Path("OpenCrew/ToolLibrary/vendor/static_ffmpeg/darwin_arm64/ffmpeg").resolve(), Path("OpenCrew/.bin/ffmpeg").resolve()):
        if candidate.exists():
            return str(candidate)
    return "ffmpeg"


def find_ffprobe() -> str:
    for candidate in (Path("OpenCrew/ToolLibrary/vendor/static_ffmpeg/darwin_arm64/ffprobe").resolve(), Path("OpenCrew/.bin/ffprobe").resolve()):
        if candidate.exists():
            return str(candidate)
    return "ffprobe"


def atempo_chain(tempo: float) -> str:
    values: list[float] = []
    remaining = max(0.01, float(tempo or 1.0))
    while remaining > 2.0:
        values.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        values.append(0.5)
        remaining /= 0.5
    values.append(remaining)
    return ",".join(f"atempo={value:.6f}" for value in values)


def run_command(command: list[str], timeout: int = 900) -> dict[str, Any]:
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
    if result.returncode != 0:
        raise ToolError(f"Command failed: {' '.join(command)}\nSTDOUT: {result.stdout[-2000:]}\nSTDERR: {result.stderr[-3000:]}")
    return {"returncode": result.returncode, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]}


def ffprobe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run([find_ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], check=True, capture_output=True, text=True, timeout=20)
        return round(float(result.stdout.strip()), 3)
    except Exception:
        return None


def ffprobe_stream_count(path: Path) -> int:
    try:
        result = subprocess.run([find_ffprobe(), "-v", "error", "-show_entries", "stream=index", "-of", "json", str(path)], check=True, capture_output=True, text=True, timeout=20)
        return len(json.loads(result.stdout).get("streams") or [])
    except Exception:
        return 0


def tempo_from_tts_selection(selection: dict[str, Any]) -> float | None:
    for value in (selection.get("tempo"), selection.get("speed_factor")):
        tempo = safe_float(value, 0.0)
        if tempo > 0:
            return tempo
    fit_meta = selection.get("fit_meta") if isinstance(selection.get("fit_meta"), dict) else {}
    for value in (fit_meta.get("tempo"), fit_meta.get("speed_factor")):
        tempo = safe_float(value, 0.0)
        if tempo > 0:
            return tempo
    candidate_id = str(selection.get("candidate_id") or "").strip()
    selected_voice = str(selection.get("voice") or selection.get("voice_id") or "").strip()
    top_candidates = selection.get("top_candidates") if isinstance(selection.get("top_candidates"), list) else []
    matched = None
    for item in top_candidates:
        if isinstance(item, dict) and candidate_id and str(item.get("candidate_id") or "").strip() == candidate_id:
            matched = item
            break
    if not matched:
        for item in top_candidates:
            if isinstance(item, dict) and selected_voice and str(item.get("voice") or item.get("voice_id") or "").strip() == selected_voice:
                matched = item
                break
    if isinstance(matched, dict):
        for value in (matched.get("tempo"), matched.get("speed_factor")):
            tempo = safe_float(value, 0.0)
            if tempo > 0:
                return tempo
        matched_fit = matched.get("fit_meta") if isinstance(matched.get("fit_meta"), dict) else {}
        for value in (matched_fit.get("tempo"), matched_fit.get("speed_factor")):
            tempo = safe_float(value, 0.0)
            if tempo > 0:
                return tempo
    return None


def apply_tts_tempo(raw_audio: Path, locked_audio: Path, tempo: float | None) -> dict[str, Any]:
    raw_duration = ffprobe_duration(raw_audio)
    tempo_value = safe_float(tempo, 0.0)
    if tempo_value <= 0:
        locked_audio.parent.mkdir(parents=True, exist_ok=True)
        if raw_audio.resolve() != locked_audio.resolve():
            shutil.copyfile(raw_audio, locked_audio)
        return {"raw_duration": raw_duration, "duration": ffprobe_duration(locked_audio), "tempo": None, "speed_factor": 1.0, "stretched": False}
    filters = [
        "aresample=48000",
        "aformat=channel_layouts=stereo",
        atempo_chain(tempo_value),
        "loudnorm=I=-17:LRA=11:TP=-1.5",
        "asetpts=N/SR/TB",
    ]
    locked_audio.parent.mkdir(parents=True, exist_ok=True)
    run_command([find_ffmpeg(), "-y", "-i", str(raw_audio), "-af", ",".join(filters), "-ar", "48000", "-ac", "2", "-vn", str(locked_audio)], timeout=180)
    return {"raw_duration": raw_duration, "duration": ffprobe_duration(locked_audio), "tempo": round(tempo_value, 4), "speed_factor": round(tempo_value, 4), "stretched": True}


def dashscope_language_type(language: str) -> str:
    mapping = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean", "de": "German", "fr": "French", "ru": "Russian", "pt": "Portuguese", "es": "Spanish", "it": "Italian"}
    return mapping.get(str(language or "").strip().lower(), language or "Chinese")


def wav_from_pcm(pcm: bytes, sample_rate: int = 24000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    out = bytearray()
    out.extend(b"RIFF")
    out.extend(struct.pack("<I", 36 + len(pcm)))
    out.extend(b"WAVEfmt ")
    out.extend(struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample))
    out.extend(b"data")
    out.extend(struct.pack("<I", len(pcm)))
    out.extend(pcm)
    return bytes(out)


def generate_tts_audio(config: dict[str, str], text_value: str, voice_id: str, prompt: str, output_path: Path) -> dict[str, Any]:
    provider = config["provider"]
    model = config["model"]
    api_key = config["api_key"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if provider == "qwen":
        input_payload: dict[str, Any] = {"text": text_value, "voice": voice_id, "language_type": dashscope_language_type("zh")}
        if "instruct" in model and prompt.strip():
            input_payload["instructions"] = prompt.strip()
            input_payload["optimize_instructions"] = True
        result = post_json_request("https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation", {"model": model, "input": input_payload}, {"Authorization": f"Bearer {api_key}"}, timeout=60)
        audio_url = first_url(result)
        if audio_url:
            download_binary(audio_url, output_path)
        else:
            audio_data = first_audio_data(result)
            if not audio_data:
                raise ToolError(f"Qwen TTS response did not include audio url/data: {json.dumps(result, ensure_ascii=False)[:1000]}")
            output_path.write_bytes(base64.b64decode(audio_data))
        return {"provider": provider, "model": model, "voice_id": voice_id, "output_path": str(output_path), "audio_url": audio_url}
    if provider == "xai":
        body, content_type = post_binary_request("https://api.x.ai/v1/tts", {"text": text_value, "voice_id": voice_id, "language": "zh", "format": "wav"}, {"Authorization": f"Bearer {api_key}"}, timeout=60)
        output_path.write_bytes(body)
        return {"provider": provider, "model": model, "voice_id": voice_id, "output_path": str(output_path), "content_type": content_type, "format": "wav"}
    if provider == "google":
        prompt_text = prompt.strip() or f"Say clearly in Mandarin Chinese, speaking exactly this text and no extra words: {text_value}"
        result = post_json_request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}",
            {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_id}}},
                },
            },
            {},
            timeout=60,
        )
        for candidate in result.get("candidates") or []:
            content = candidate.get("content") if isinstance(candidate, dict) else {}
            for part in content.get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data") or {}
                encoded = str(inline.get("data") or "") if isinstance(inline, dict) else ""
                if not encoded:
                    continue
                mime_type = str(inline.get("mimeType") or inline.get("mime_type") or "audio/wav")
                raw = base64.b64decode(encoded)
                output_path.write_bytes(wav_from_pcm(raw) if "pcm" in mime_type or "l16" in mime_type else raw)
                return {"provider": provider, "model": model, "voice_id": voice_id, "output_path": str(output_path), "mime_type": mime_type}
        raise ToolError("Google Gemini TTS response did not include audio data")
    raise ToolError(f"Unsupported TTS provider: {provider}/{model}")


def load_plan(workspace: Path, input_name: str) -> dict[str, Any]:
    path = workspace / input_name
    if not path.exists():
        raise ToolError(f"missing shot plan: {path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ToolError(f"shot plan must be an object: {path}")
    return payload


def load_source_package(workspace: Path, source_name: str) -> dict[str, Any]:
    for path in (workspace / source_name, workspace / "rebuild" / source_name):
        if path.exists():
            payload = read_json(path)
            return payload if isinstance(payload, dict) else {}
    return {}


def write_plan_atomic(path: Path, plan: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("shots", []) if isinstance(item, dict)]


def shot_id_of(shot: dict[str, Any]) -> str:
    return str(shot.get("shot_id") or shot.get("id") or "")


def scene_id_of(mark: dict[str, Any]) -> str:
    return str(mark.get("scene_mark_id") or mark.get("id") or "")


def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    ref = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    return [item for item in ref.get("scene_marks", []) if isinstance(item, dict)]


def target_shots(plan: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    wanted = {str(item) for item in getattr(args, "shot_id", []) if str(item)}
    shots = [shot for shot in shot_list(plan) if not wanted or shot_id_of(shot) in wanted]
    if wanted and not shots:
        raise ToolError(f"No shots matched --shot-id: {sorted(wanted)}")
    if len(shots) != 1:
        raise ToolError("PlanD is a shot-level tool and requires exactly one --shot-id")
    return shots


def strip_srt_timing(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def spoken_script(shot: dict[str, Any]) -> str:
    for key in ("spoken_script", "script", "srt_text"):
        value = str(shot.get(key) or "").strip()
        if value:
            return strip_srt_timing(value)
    return ""


def scene_text(mark: dict[str, Any], fallback: str = "") -> str:
    for key in ("srt_text", "scene_srt", "text", "subtitle"):
        value = str(mark.get(key) or "").strip()
        if value:
            return value
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    for key in ("srt_text", "summary", "video_prompt", "prompt"):
        value = str(desc.get(key) or "").strip()
        if value:
            return value
    return str(fallback or "").strip()


def shot_tts_source(shot: dict[str, Any]) -> dict[str, Any]:
    scene_items = []
    for mark in scene_marks_for_shot(shot):
        text = scene_text(mark, "")
        if text:
            scene_items.append({"scene_mark_id": scene_id_of(mark), "text": text})
    text_value = " ".join(item["text"] for item in scene_items).strip() or spoken_script(shot)
    return {"text": strip_srt_timing(text_value), "source": "scene_srt" if scene_items else "spoken_script", "scene_items": scene_items}


def variant_shot_dir(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return workspace / "Assets" / variant_id / safe_name(shot_id)


def variant_scene_dir(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / safe_name(scene_mark_id)


def canonical_scene_image_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "first.png"


def scene_asset_manifest_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "asset_manifest.json"


def shot_tts_dir(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / "tts"


def canonical_locked_tts_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "locked.wav"


def canonical_shot_srt_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "shot.srt"


def canonical_shot_tts_timeline_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "shot_tts_timeline.locked.json"


def scene_tts_locked_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "tts" / "locked.wav"


def scene_tts_manifest_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "tts" / "tts_manifest.json"


def final_prompt_package_path(workspace: Path, shot: dict[str, Any], variant_id: str = "variant_001") -> Path:
    ref = shot.get("final_prompt_package") if isinstance(shot.get("final_prompt_package"), dict) else {}
    rel_path = str(ref.get("path") or "").strip()
    if rel_path:
        return resolve_workspace_path(workspace, rel_path)
    return variant_shot_dir(workspace, shot_id_of(shot), variant_id) / "final_prompt_package.json"


def plan_d_raw_video_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / "plan_d_raw.mp4"


def plan_d_scene_raw_video_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "plan_d_raw.mp4"


def plan_d_scene_final_video_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "plan_d.mp4"


def plan_d_shot_video_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / "plan_d.mp4"


def plan_d_prompt_text_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / "plan_d_video_prompt_timed.txt"


def plan_d_prompt_json_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / "plan_d_video_prompt_timed.json"


def plan_d_reports_dir(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / "reports"


def plan_d_report_path(workspace: Path, shot_id: str, filename: str, variant_id: str = "variant_001") -> Path:
    return plan_d_reports_dir(workspace, shot_id, variant_id) / filename


def srt_seconds(timestamp: str) -> float:
    match = re.match(r"^\s*(\d+):(\d+):(\d+)[,.](\d+)\s*$", timestamp)
    if not match:
        return 0.0
    hours, minutes, seconds, millis = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")[:3]) / 1000


def srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        secs += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def parse_srt_entries(text: str) -> list[dict[str, Any]]:
    entries = []
    for block in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing = next((line for line in lines if "-->" in line), "")
        if not timing:
            continue
        start_raw, end_raw = [part.strip().split()[0] for part in timing.split("-->", 1)]
        start = srt_seconds(start_raw)
        end = srt_seconds(end_raw)
        body = " ".join(line for line in lines if line != timing and not line.isdigit()).strip()
        if body:
            entries.append({"index": len(entries) + 1, "start": round(start, 3), "end": round(max(end, start), 3), "duration": round(max(0.0, end - start), 3), "text": body, "timing": timing})
    return entries


def srt_from_entries(entries: list[dict[str, Any]]) -> str:
    blocks = []
    for index, entry in enumerate(entries, start=1):
        blocks.append(f"{index}\n{srt_timestamp(safe_float(entry.get('start')))} --> {srt_timestamp(safe_float(entry.get('end')))}\n{entry.get('text') or ''}")
    return "\n\n".join(blocks).strip() + "\n"


def concat_file_line(path: Path) -> str:
    return "file '" + str(path).replace("'", "'\\''") + "'"


def scene_lookup(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {}
    for scene in package.get("scenes") or []:
        if isinstance(scene, dict):
            scene_id = str(scene.get("scene_mark_id") or scene.get("id") or "").strip()
            if scene_id:
                rows[scene_id] = scene
    return rows


def text_from_scene_tts_manifest(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        manifest = read_json(path)
    except Exception:
        return ""
    if not isinstance(manifest, dict):
        return ""
    for key in ("text", "srt_text", "narration", "script"):
        value = str(manifest.get(key) or "").strip()
        if value:
            return strip_srt_timing(value)
    return ""


def scene_tts_segments(workspace: Path, shot: dict[str, Any], package: dict[str, Any], variant_id: str) -> list[dict[str, Any]]:
    shot_id = shot_id_of(shot)
    package_scenes = scene_lookup(package)
    segments = []
    cursor = 0.0
    for index, mark in enumerate(scene_marks_for_shot(shot), start=1):
        scene_id = scene_id_of(mark) or f"{shot_id}_scene_{index:03d}"
        audio_path = scene_tts_locked_path(workspace, shot_id, scene_id, variant_id)
        duration = ffprobe_duration(audio_path) if audio_path.exists() else None
        package_scene = package_scenes.get(scene_id) or {}
        text_value = (
            text_from_scene_tts_manifest(scene_tts_manifest_path(workspace, shot_id, scene_id, variant_id))
            or strip_srt_timing(str(package_scene.get("srt_text") or ""))
            or strip_srt_timing(scene_text(mark, ""))
        )
        start = round(cursor, 3)
        end = round(cursor + safe_float(duration, 0.0), 3)
        segments.append({
            "index": index,
            "scene_mark_id": scene_id,
            "audio_path": audio_path,
            "audio": rel(workspace, audio_path),
            "duration": duration,
            "start": start,
            "end": end,
            "text": text_value,
        })
        cursor = end
    return segments


def validate_scene_tts_segments(segments: list[dict[str, Any]]) -> list[str]:
    missing = []
    for segment in segments:
        scene_id = str(segment.get("scene_mark_id") or "scene")
        audio_path = segment.get("audio_path")
        if not isinstance(audio_path, Path) or not audio_path.exists() or audio_path.stat().st_size <= 0:
            missing.append(f"{scene_id}: missing_scene_locked_tts")
            continue
        duration = safe_float(segment.get("duration"), 0.0)
        if duration <= 0:
            missing.append(f"{scene_id}: unreadable_scene_locked_tts_duration")
        if not str(segment.get("text") or "").strip():
            missing.append(f"{scene_id}: missing_scene_tts_text")
    return missing


def scene_segments_to_srt_entries(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = []
    for index, segment in enumerate(segments, start=1):
        start = safe_float(segment.get("start"), 0.0)
        end = safe_float(segment.get("end"), start)
        entries.append({
            "index": index,
            "scene_mark_id": segment.get("scene_mark_id") or "",
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.05, end - start), 3),
            "text": segment.get("text") or "",
            "timing": f"{srt_timestamp(start)} --> {srt_timestamp(end)}",
        })
    return entries


def temporary_lipsync_audio_path(shot_id: str) -> Path:
    tmp_root = Path(os.environ.get("TMPDIR") or "/private/tmp") / "opencrew_plan_d_lipsync_audio"
    tmp_root.mkdir(parents=True, exist_ok=True)
    return tmp_root / f"{safe_name(shot_id)}_{uuid.uuid4().hex}.wav"


def compose_scene_tts_audio(workspace: Path, shot_id: str, segments: list[dict[str, Any]], output_path: Path) -> dict[str, Any]:
    concat_path = output_path.with_suffix(".concat.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(concat_path, "\n".join(concat_file_line(segment["audio_path"]) for segment in segments) + "\n")
    run_command([
        find_ffmpeg(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-af",
        "aresample=48000,aformat=channel_layouts=stereo,loudnorm=I=-17:LRA=11:TP=-1.5,asetpts=N/SR/TB",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-vn",
        str(output_path),
    ], timeout=300)
    expected_duration = round(sum(safe_float(segment.get("duration"), 0.0) for segment in segments), 3)
    actual_duration = ffprobe_duration(output_path)
    return {
        "source": "temporary_scene_locked_tts_concat",
        "shot_id": shot_id,
        "output_path": str(output_path),
        "expected_duration": expected_duration,
        "duration": actual_duration,
        "duration_delta": round(abs(safe_float(actual_duration, 0.0) - expected_duration), 3) if actual_duration else None,
        "concat_file": str(concat_path),
    }


def ensure_shot_srt_text(shot: dict[str, Any], package: dict[str, Any]) -> str:
    for key in ("srt_text", "shot_srt"):
        text = str(shot.get(key) or "").strip()
        if text and "-->" in text:
            return text
    scene_texts = []
    for scene in package.get("scenes") or []:
        if isinstance(scene, dict):
            value = str(scene.get("srt_text") or "").strip()
            if value:
                scene_texts.append(value)
    combined = "\n".join(scene_texts).strip()
    if "-->" in combined:
        return combined
    for mark in scene_marks_for_shot(shot):
        value = str(mark.get("srt_text") or "").strip()
        if value and "-->" in value:
            return value
    duration = max(0.1, safe_float(shot.get("duration"), 1.0))
    clean = strip_srt_timing(combined or shot_tts_source(shot).get("text") or spoken_script(shot))
    return f"1\n00:00:00,000 --> {srt_timestamp(duration)}\n{clean}\n"


def normalize_srt_to_audio(entries: list[dict[str, Any]], audio_duration: float) -> list[dict[str, Any]]:
    if not entries:
        return []
    source_start = min(safe_float(item.get("start"), 0.0) for item in entries)
    source_end = max(safe_float(item.get("end"), 0.0) for item in entries)
    source_duration = max(0.1, source_end - source_start)
    scale = max(0.1, audio_duration) / source_duration
    normalized = []
    for index, entry in enumerate(entries, start=1):
        start = round(max(0.0, (safe_float(entry.get("start")) - source_start) * scale), 3)
        end = round(max(start + 0.05, (safe_float(entry.get("end")) - source_start) * scale), 3)
        if index == len(entries):
            end = round(max(end, audio_duration), 3)
        normalized.append({**entry, "index": index, "start": start, "end": min(round(audio_duration, 3), end), "duration": round(max(0.05, min(audio_duration, end) - start), 3)})
    return normalized


def compile_structured_video_prompt(scene: dict[str, Any]) -> str:
    structured_all = scene.get("video_prompt_structured") if isinstance(scene.get("video_prompt_structured"), dict) else {}
    language = "zh" if str(scene.get("active_language") or "").strip().lower() == "zh" else "en"
    structured = structured_all.get(language) if isinstance(structured_all.get(language), dict) else {}
    labels = {
        "zh": {
            "positive": "正向",
            "character_action": "人物动作",
            "speech_speed": "语言速度",
            "voice_description": "语音描述",
            "camera_motion": "镜头运动",
            "scene_consistency": "场景一致性",
            "product_consistency": "产品一致性",
            "negative": "负向",
            "model_notes": "模型备注",
        },
        "en": {
            "positive": "Positive",
            "character_action": "Character Action",
            "speech_speed": "Speech Speed",
            "voice_description": "Voice Description",
            "camera_motion": "Camera Motion",
            "scene_consistency": "Scene Consistency",
            "product_consistency": "Product Consistency",
            "negative": "Negative",
            "model_notes": "Model Notes",
        },
    }
    parts = []
    for key in VIDEO_PROMPT_FIELDS:
        value = str(structured.get(key) or "").strip()
        if value:
            parts.append(f"{labels[language][key]}:\n{value}")
    return "\n\n".join(parts).strip()


def scene_video_prompt(scene: dict[str, Any]) -> str:
    structured = compile_structured_video_prompt(scene)
    if structured:
        return structured
    for key in ("plan_d_video_prompt", "video_prompt", "grok_video_prompt", "prompt"):
        value = str(scene.get(key) or "").strip()
        if value:
            return value
    return str(scene.get("srt_text") or "").strip()


def one_line_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clipped_text(value: Any, max_chars: int) -> str:
    text = one_line_text(value)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def video_prompt_limit(config: dict[str, str]) -> int:
    provider = str(config.get("provider") or "").strip().lower()
    if provider == "xai":
        return 3900
    return 0


def prompt_length(text: str) -> int:
    return len(str(text or "").encode("utf-8"))


def trim_prompt_to_limit(text: str, max_bytes: int) -> str:
    if max_bytes <= 0 or prompt_length(text) <= max_bytes:
        return text
    suffix = "..."
    budget = max(0, max_bytes - len(suffix.encode("utf-8")))
    return str(text or "").encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip() + suffix


def join_lines_with_budget(lines: list[str], max_chars: int) -> str:
    if max_chars <= 0:
        return "\n".join(lines)
    used = 0
    selected = []
    for line in lines:
        candidate = str(line or "").strip()
        if not candidate:
            continue
        cost = len(candidate) + (1 if selected else 0)
        if used + cost > max_chars:
            remaining = max_chars - used - (1 if selected else 0)
            if remaining > 20:
                selected.append(clipped_text(candidate, remaining))
            selected.append(f"- ... {max(0, len(lines) - len(selected))} more timing lines")
            break
        selected.append(candidate)
        used += cost
    return "\n".join(selected)


def compact_timed_video_prompt(full_text: str, video_config: dict[str, str], shot_id: str, provider: str, audio_duration: float, reference_lines: list[str], timing_lines: list[str], scene_prompts: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    limit = video_prompt_limit(video_config)
    metadata = {
        "compacted": False,
        "original_length": prompt_length(full_text),
        "prompt_limit": limit or None,
    }
    if not limit or prompt_length(full_text) <= limit:
        metadata["final_length"] = prompt_length(full_text)
        return full_text, metadata
    scene_budget = 1300
    per_scene = max(180, min(520, scene_budget // max(1, len(scene_prompts))))
    compact_scene_lines = []
    for item in scene_prompts:
        compact_scene_lines.append(f"- Scene {item.get('index')} {item.get('scene_mark_id')}: {clipped_text(item.get('prompt'), per_scene)}")
    compact_text = f"""Create a realistic vertical 9:16 product video for Shot {shot_id}.
Provider/model: {provider}
Duration: {audio_duration:.2f} seconds.

Must follow:
- Use the provided reference image(s) as exact anchors for visible people, product packaging, room, lighting, and phone-camera realism.
- If a scene reference has a visible speaker head/face, preserve that person and allow subtle speaking motion. If a scene reference is product-only or has no head/face, do not introduce any human head, face, portrait, mouth, eyes, or upper body; focus on product/object presentation.
- Keep subtle natural motion only: blinking and small head movement for visible speakers, or gentle hand/product movement for product-only scenes.
- No subtitles, captions, logos, watermarks, UI text, extra on-screen text, scene cuts, large camera moves, or conflicting generated audio.

Reference images:
{chr(10).join(reference_lines) if reference_lines else "- No reference image available; generate from prompt only."}

Locked TTS timing:
{join_lines_with_budget(timing_lines, 950)}

Scene continuity:
{chr(10).join(compact_scene_lines)}
""".strip()
    if prompt_length(compact_text) > limit:
        compact_scene_lines = []
        per_scene = max(120, min(260, 760 // max(1, len(scene_prompts))))
        for item in scene_prompts:
            compact_scene_lines.append(f"- Scene {item.get('index')} {item.get('scene_mark_id')}: {clipped_text(item.get('prompt'), per_scene)}")
        compact_text = f"""Create a realistic vertical 9:16 product video for Shot {shot_id}. Duration {audio_duration:.2f}s.
Use reference image(s) as exact anchors. Visible speaker face means preserve the person and allow subtle speaking motion; product-only or no-face reference means no human head/face/portrait/upper body and focus on product/object presentation. No subtitles, captions, logos, watermarks, UI text, scene cuts, large camera moves, or conflicting audio.

Reference images:
{chr(10).join(reference_lines) if reference_lines else "- No reference image available."}

TTS timing:
{join_lines_with_budget(timing_lines, 650)}

Scene notes:
{chr(10).join(compact_scene_lines)}
""".strip()
    compact_text = trim_prompt_to_limit(compact_text, limit)
    metadata.update({
        "compacted": True,
        "reason": "provider_prompt_limit",
        "final_length": prompt_length(compact_text),
    })
    return compact_text, metadata


def prompt_by_scene_id(scene_prompts: list[dict[str, Any]]) -> dict[str, str]:
    rows = {}
    for item in scene_prompts:
        scene_id = str(item.get("scene_mark_id") or "").strip()
        if scene_id:
            rows[scene_id] = str(item.get("prompt") or "").strip()
    return rows


def reference_image_by_scene_id(reference_images: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = {}
    for item in reference_images:
        scene_id = str(item.get("scene_mark_id") or "").strip()
        if scene_id:
            rows[scene_id] = item
    return rows


def build_scene_raw_video_prompt(
    shot_id: str,
    scene_entry: dict[str, Any],
    scene_prompt: str,
    reference_image: dict[str, Any],
    video_config: dict[str, str],
) -> str:
    scene_id = str(scene_entry.get("scene_mark_id") or "")
    provider = f"{video_config.get('provider')}/{video_config.get('model')}"
    duration = safe_float(scene_entry.get("duration"), 0.0)
    text = f"""Create a realistic vertical 9:16 product scene video for Shot {shot_id}, Scene {scene_id}.
Provider/model: {provider}
Scene duration: {duration:.2f} seconds.
Shot timeline window: {safe_float(scene_entry.get('start')):.2f}s-{safe_float(scene_entry.get('end')):.2f}s.

Hard requirements:
- Use this scene's reference image as the exact anchor for visible people, product packaging, objects, room, lighting, camera angle, and phone-camera realism.
- Generate only this scene, not the entire shot.
- If the reference image clearly shows a visible speaker's head/face, preserve that person and allow natural speaking motion during this scene. Exact lip sync will be repaired later from the scene TTS audio.
- If the reference image is product-only, object-only, or does not show a head/face, do not introduce any human head, face, portrait, mouth, eyes, or upper body. Keep the scene focused on product/object presentation; hands may appear only when already implied by the reference and only as product-operating hands.
- Keep subtle natural motion only: natural blinking and small head movement for visible speakers, or gentle product/hand movement for product-only scenes.
- No subtitles, captions, logos, watermarks, UI text, scene cuts, large camera moves, or conflicting generated audio.

Reference image:
- {reference_image.get('scene_mark_id') or scene_id}: {reference_image.get('rel') or ''}

Scene TTS text:
{scene_entry.get('text') or ''}

Scene visual prompt:
{scene_prompt or 'Realistic vertical phone-camera product scene video anchored to the reference image.'}
""".strip()
    limit = video_prompt_limit(video_config)
    if limit:
        text = trim_prompt_to_limit(text, limit)
    return text


def concat_scene_raw_videos(workspace: Path, videos: list[Path], output_path: Path) -> dict[str, Any]:
    concat_path = output_path.with_suffix(".scene.concat.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(concat_path, "\n".join(concat_file_line(path) for path in videos) + "\n")
    run_command([
        find_ffmpeg(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-vf",
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-an",
        "-movflags",
        "+faststart",
        str(output_path),
    ], timeout=1200)
    return {
        "source": "scene_raw_video_concat",
        "output_path": str(output_path),
        "output_video": rel(workspace, output_path),
        "concat_file": rel(workspace, concat_path),
        "duration": ffprobe_duration(output_path),
        "scene_video_count": len(videos),
    }


def concat_scene_final_videos(workspace: Path, videos: list[Path], output_path: Path) -> dict[str, Any]:
    concat_path = output_path.with_suffix(".scene_final.concat.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(concat_path, "\n".join(concat_file_line(path) for path in videos) + "\n")
    run_command([
        find_ffmpeg(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-vf",
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ], timeout=1200)
    return {
        "source": "scene_final_video_concat",
        "output_path": str(output_path),
        "output_video": rel(workspace, output_path),
        "concat_file": rel(workspace, concat_path),
        "duration": ffprobe_duration(output_path),
        "scene_video_count": len(videos),
    }


def mux_video_with_audio(workspace: Path, video_path: Path, audio_path: Path, output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_duration = ffprobe_duration(video_path) or 0.0
    audio_duration = ffprobe_duration(audio_path) or 0.0
    pad_duration = max(0.0, video_duration - audio_duration)
    run_command([
        find_ffmpeg(),
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-filter:a",
        f"apad=pad_dur={pad_duration:.3f}",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ], timeout=1200)
    return {
        "source": "no_lipsync_voiceover_mux",
        "video": rel(workspace, video_path),
        "audio": rel(workspace, audio_path) if audio_path.is_relative_to(workspace) else str(audio_path),
        "output_path": str(output_path),
        "output_video": rel(workspace, output_path),
        "duration": ffprobe_duration(output_path),
        "video_duration": video_duration,
        "audio_duration": audio_duration,
        "audio_pad_duration": round(pad_duration, 3),
    }


def generate_scene_raw_videos(
    workspace: Path,
    shot_id: str,
    video_config: dict[str, str],
    normalized_entries: list[dict[str, Any]],
    reference_images: list[dict[str, Any]],
    scene_prompts: list[dict[str, Any]],
    output_path: Path,
    variant_id: str,
) -> dict[str, Any]:
    prompts = prompt_by_scene_id(scene_prompts)
    images = reference_image_by_scene_id(reference_images)
    scene_results = []
    scene_videos = []
    for index, entry in enumerate(normalized_entries, start=1):
        scene_id = str(entry.get("scene_mark_id") or f"{shot_id}_scene_{index:03d}")
        image = images.get(scene_id)
        if not image and reference_images:
            image = reference_images[min(index - 1, len(reference_images) - 1)]
        if not image or not image.get("path"):
            raise ToolError(f"{scene_id}: missing_scene_reference_image_for_plan_d_video")
        scene_raw = plan_d_scene_raw_video_path(workspace, shot_id, scene_id, variant_id)
        scene_prompt = build_scene_raw_video_prompt(shot_id, entry, prompts.get(scene_id, ""), image, video_config)
        scene_prompt_path = scene_raw.with_suffix(".prompt.txt")
        write_text(scene_prompt_path, scene_prompt)
        result = generate_video(video_config, scene_prompt, scene_raw, [image["path"]], safe_float(entry.get("duration"), 1.0))
        scene_videos.append(scene_raw)
        scene_results.append({
            "scene_mark_id": scene_id,
            "scene_raw_video": rel(workspace, scene_raw),
            "scene_prompt": rel(workspace, scene_prompt_path),
            "target_start": entry.get("start"),
            "target_end": entry.get("end"),
            "target_duration": entry.get("duration"),
            "video": result,
        })
    concat_result = concat_scene_raw_videos(workspace, scene_videos, output_path)
    return {
        "source": "scene_level_plan_d_video_generation",
        "mode": "one_video_call_per_scene_then_shot_concat",
        "scenes": scene_results,
        "concat": concat_result,
        "duration": concat_result.get("duration"),
        "output_path": str(output_path),
    }


def run_scene_lipsync_then_concat(
    workspace: Path,
    shot_id: str,
    config: dict[str, str],
    normalized_entries: list[dict[str, Any]],
    final_video: Path,
    variant_id: str,
) -> dict[str, Any]:
    scene_results = []
    scene_final_videos = []
    for index, entry in enumerate(normalized_entries, start=1):
        scene_id = str(entry.get("scene_mark_id") or f"{shot_id}_scene_{index:03d}")
        raw_video = plan_d_scene_raw_video_path(workspace, shot_id, scene_id, variant_id)
        audio_path = scene_tts_locked_path(workspace, shot_id, scene_id, variant_id)
        scene_final = plan_d_scene_final_video_path(workspace, shot_id, scene_id, variant_id)
        if not raw_video.exists() or raw_video.stat().st_size <= 0:
            raise ToolError(f"{scene_id}: missing_scene_plan_d_raw_video")
        if not audio_path.exists() or audio_path.stat().st_size <= 0:
            raise ToolError(f"{scene_id}: missing_scene_locked_tts")
        reports = scene_final.parent / "reports"
        request_path = reports / "plan_d_scene_lipsync_request.json"
        status_path = reports / "plan_d_scene_lipsync_status.json"
        create_path = reports / "plan_d_scene_lipsync_create_response.json"
        lipsync_result = run_sync_lipsync(config, raw_video, audio_path, scene_final, request_path, status_path, create_path)
        scene_final_videos.append(scene_final)
        scene_results.append({
            "scene_mark_id": scene_id,
            "raw_video": rel(workspace, raw_video),
            "audio": rel(workspace, audio_path),
            "output_video": rel(workspace, scene_final),
            "target_start": entry.get("start"),
            "target_end": entry.get("end"),
            "target_duration": entry.get("duration"),
            "duration": ffprobe_duration(scene_final),
            "lipsync": lipsync_result,
        })
    concat_result = concat_scene_final_videos(workspace, scene_final_videos, final_video)
    manifest = {
        "source": "scene_level_lipsync_then_concat",
        "mode": "one_lipsync_call_per_scene_then_shot_concat",
        "scenes": scene_results,
        "concat": concat_result,
        "duration": concat_result.get("duration"),
        "output_path": str(final_video),
    }
    write_json(plan_d_report_path(workspace, shot_id, "plan_d_scene_level_lipsync_then_concat.json", variant_id), {
        "status": "completed",
        "shot_id": shot_id,
        **manifest,
        "generated_at": now_ms(),
    })
    return manifest


def explicit_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "y", "1", "required", "need", "needs", "on"}:
            return True
        if text in {"false", "no", "n", "0", "skip", "skipped", "none", "off", "not_required"}:
            return False
    return None


def scene_lipsync_explicit(scene: dict[str, Any]) -> tuple[bool | None, str]:
    field_names = (
        "needs_lipsync",
        "need_lipsync",
        "requires_lipsync",
        "require_lipsync",
        "lipsync_required",
        "lip_sync_required",
        "mouth_sync_required",
        "needs_mouth_sync",
        "host_speaking_visible",
        "visible_speaker",
        "talking_head",
    )
    containers = [scene]
    for key in ("plan_d", "video_task", "video_generation", "generation_task", "task", "metadata"):
        value = scene.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for field in field_names:
            if field in container:
                parsed = explicit_bool(container.get(field))
                if parsed is not None:
                    return parsed, f"explicit:{field}"
    return None, ""


def scene_text_for_lipsync(scene: dict[str, Any]) -> str:
    chunks = []
    final_prompts = scene.get("final_prompts") if isinstance(scene.get("final_prompts"), dict) else {}
    structured = scene.get("video_prompt_structured") if isinstance(scene.get("video_prompt_structured"), dict) else {}
    for source in (scene, final_prompts, structured):
        for key in ("image_prompt", "video_prompt", "grok_video_prompt", "prompt", "positive", "character_action", "scene_locks", "host_locks", "srt_text", "original_srt_text"):
            value = source.get(key) if isinstance(source, dict) else None
            if value:
                chunks.append(str(value))
    return "\n".join(chunks)


def scene_needs_lipsync(scene: dict[str, Any]) -> tuple[bool, str]:
    explicit, reason = scene_lipsync_explicit(scene)
    if explicit is not None:
        return explicit, reason
    text = scene_text_for_lipsync(scene).lower()
    no_lipsync_patterns = (
        "tight product close-up",
        "product close-up",
        "close-up of hands",
        "visible hands",
        "if any face",
        "only for visible hand",
        "no visible face",
        "no face visible",
        "hands-only",
        "product-only",
        "产品特写",
        "手部特写",
        "只有手",
        "无人物口播",
        "无需对口型",
        "不需要对口型",
    )
    positive_patterns = (
        "mouth movement",
        "mouth continuity",
        "lip-sync",
        "lip sync",
        "lipsync",
        "speaking expression",
        "host is speaking",
        "host naturally speaking",
        "mid-speech",
        "talking-head",
        "talking head",
        "faces camera",
        "face in the upper half",
        "visible speaker",
        "口型",
        "对口型",
        "口播",
        "人物自然口播",
        "面对镜头",
    )
    if any(pattern in text for pattern in no_lipsync_patterns):
        return False, "inferred_no_visible_speaker"
    if any(pattern in text for pattern in positive_patterns):
        return True, "inferred_visible_speaker"
    if str(scene.get("srt_text") or scene.get("original_srt_text") or "").strip():
        return True, "default_srt_requires_lipsync"
    return False, "no_scene_lipsync_task"


def shot_lipsync_plan(shot: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    scenes = [item for item in (package.get("scenes") or []) if isinstance(item, dict)]
    if not scenes:
        ref = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
        scenes = [item for item in (ref.get("scene_marks") or []) if isinstance(item, dict)]
    decisions = []
    for index, scene in enumerate(scenes, start=1):
        required, reason = scene_needs_lipsync(scene)
        decisions.append({
            "scene_mark_id": str(scene.get("scene_mark_id") or f"{shot_id_of(shot)}_scene_{index:03d}"),
            "needs_lipsync": required,
            "reason": reason,
        })
    required = any(item["needs_lipsync"] for item in decisions) if decisions else True
    return {
        "needs_lipsync": required,
        "reason": "at_least_one_scene_needs_lipsync" if required else "no_scene_needs_lipsync",
        "scene_decisions": decisions,
    }


def load_final_prompt_package(workspace: Path, shot: dict[str, Any], variant_id: str) -> tuple[dict[str, Any], Path]:
    path = final_prompt_package_path(workspace, shot, variant_id)
    if path.exists():
        package = read_json(path)
        if isinstance(package, dict):
            return package, path
    scenes = []
    for mark in scene_marks_for_shot(shot):
        final_prompts = mark.get("final_prompts") if isinstance(mark.get("final_prompts"), dict) else {}
        scenes.append({
            "scene_mark_id": scene_id_of(mark),
            "srt_text": scene_text(mark, ""),
            "video_prompt": final_prompts.get("video_prompt") or final_prompts.get("grok_video_prompt") or "",
            "grok_video_prompt": final_prompts.get("grok_video_prompt") or final_prompts.get("video_prompt") or "",
            "active_language": final_prompts.get("active_language") or "en",
            "video_prompt_structured": final_prompts.get("video_prompt_structured") if isinstance(final_prompts.get("video_prompt_structured"), dict) else {},
            "negative_prompt": final_prompts.get("negative_prompt") or "",
        })
    package = {"shot_id": shot_id_of(shot), "tts": {}, "scenes": scenes}
    return package, path


def reference_images_for_shot(workspace: Path, shot: dict[str, Any], variant_id: str) -> list[dict[str, Any]]:
    shot_id = shot_id_of(shot)
    rows = []
    for mark in scene_marks_for_shot(shot):
        scene_id = scene_id_of(mark)
        image_path = canonical_scene_image_path(workspace, shot_id, scene_id, variant_id)
        if image_path.exists():
            rows.append({"scene_mark_id": scene_id, "path": image_path, "rel": rel(workspace, image_path), "text": scene_text(mark, "")})
    if rows:
        return rows
    candidate = variant_shot_dir(workspace, shot_id, variant_id) / "first.png"
    if candidate.exists():
        return [{"scene_mark_id": f"{shot_id}_scene_001", "path": candidate, "rel": rel(workspace, candidate), "text": spoken_script(shot)}]
    return []


def video_capability(config: dict[str, str]) -> dict[str, Any]:
    provider = str(config.get("provider") or "")
    model = str(config.get("model") or "")
    if provider == "xai":
        return {"provider": provider, "model": model, "min_duration": 1, "max_duration": 15, "max_reference_images": 4}
    if provider == "gemini":
        return {"provider": provider, "model": model, "min_duration": 4, "max_duration": 8, "max_reference_images": 3}
    if provider == "wan":
        return {"provider": provider, "model": model, "min_duration": 3, "max_duration": 15 if "happyhorse" in model else 30, "max_reference_images": 3 if "r2v" in model else 1}
    if provider == "openai":
        return {"provider": provider, "model": model, "min_duration": 4, "max_duration": 20, "max_reference_images": 1}
    return {"provider": provider, "model": model, "min_duration": 1, "max_duration": 8, "max_reference_images": 1}


def provider_video_seconds(config: dict[str, str], duration: float | int | str | None) -> int:
    requested = int(math.ceil(max(0.1, safe_float(duration, 4.0))))
    capability = video_capability(config)
    return max(int(capability["min_duration"]), min(requested, int(capability["max_duration"])))


def provider_reference_video_max_seconds(config: dict[str, str]) -> int:
    provider = str(config.get("provider") or "")
    if provider == "xai":
        return 10
    return int(video_capability(config).get("max_duration") or 8)


def image_inline_payload(path: Path | None) -> dict[str, str] | None:
    if not path:
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return {"mimeType": mime, "bytesBase64Encoded": base64.b64encode(path.read_bytes()).decode("ascii")}


def dashscope_upload_file(api_key: str, model: str, path: Path) -> str:
    query = urllib.parse.urlencode({"action": "getPolicy", "model": model})
    policy = get_json_request(f"https://dashscope.aliyuncs.com/api/v1/uploads?{query}", {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    policy_data = policy.get("data") if isinstance(policy.get("data"), dict) else {}
    upload_host = str(policy_data.get("upload_host") or "")
    upload_dir = str(policy_data.get("upload_dir") or "")
    if not upload_host or not upload_dir:
        raise ToolError(f"DashScope upload policy is missing upload_host/upload_dir: {json.dumps(policy, ensure_ascii=False)[:1000]}")
    key = f"{upload_dir.rstrip('/')}/{path.name}"
    boundary = f"----OpenCrewDashScope{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    fields = {
        "OSSAccessKeyId": str(policy_data.get("oss_access_key_id") or ""),
        "Signature": str(policy_data.get("signature") or ""),
        "policy": str(policy_data.get("policy") or ""),
        "x-oss-object-acl": str(policy_data.get("x_oss_object_acl") or "private"),
        "x-oss-forbid-overwrite": str(policy_data.get("x_oss_forbid_overwrite") or "true"),
        "key": key,
        "success_action_status": "200",
    }
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode("utf-8"), b"\r\n"])
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), path.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()])
    req = urllib.request.Request(upload_host, data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            if res.status != 200:
                raise ToolError(f"DashScope upload failed: HTTP {res.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"DashScope upload failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"DashScope upload failed: {exc.reason}") from exc
    return f"oss://{key}"


def generate_video(config: dict[str, str], prompt: str, output_path: Path, reference_images: list[Path], duration: float) -> dict[str, Any]:
    provider = str(config.get("provider") or "")
    model = str(config.get("model") or "")
    api_key = str(config.get("api_key") or "")
    seconds = provider_video_seconds(config, duration)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    video_url = ""
    if provider == "xai":
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "duration": seconds, "aspect_ratio": "9:16", "resolution": "720p"}
        capability = video_capability(config)
        refs = reference_images[: int(capability.get("max_reference_images") or 1)]
        if len(refs) == 1:
            inline = image_inline_payload(refs[0])
            if inline:
                payload["image"] = {"url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}"}
        elif refs:
            payload["reference_images"] = []
            for image_path in refs:
                inline = image_inline_payload(image_path)
                if inline:
                    payload["reference_images"].append({"url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}"})
        started = post_json_request("https://api.x.ai/v1/videos/generations", payload, {"Authorization": f"Bearer {api_key}"}, timeout=120)
        video_id = str(started.get("request_id") or started.get("id") or ((started.get("data") or {}).get("id") if isinstance(started.get("data"), dict) else "") or "")
        if not video_id:
            video_url = first_url(started)
        deadline = time.time() + 900
        while not video_url and video_id and time.time() < deadline:
            polled = get_json_request(f"https://api.x.ai/v1/videos/{urllib.parse.quote(video_id, safe='')}", {"Authorization": f"Bearer {api_key}"}, timeout=120)
            failure = operation_failed(polled)
            if failure:
                raise ToolError(f"xAI video generation failed: {failure}")
            if str(polled.get("status") or "").lower() == "done" or operation_done(polled):
                video_url = first_url(polled)
                break
            time.sleep(5)
        if not video_url:
            raise ToolError("xAI video generation completed without a downloadable video URL")
        download_binary(video_url, output_path, {"Authorization": f"Bearer {api_key}"})
    elif provider == "openai":
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "seconds": str(seconds), "size": "720x1280"}
        inline = image_inline_payload(reference_images[0] if reference_images else None)
        if inline:
            payload["input_reference"] = {"image_url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}"}
        started = post_json_request("https://api.openai.com/v1/videos", payload, {"Authorization": f"Bearer {api_key}"}, timeout=180)
        video_id = str(started.get("id") or "")
        if not video_id:
            raise ToolError(f"OpenAI video response did not include id: {json.dumps(started, ensure_ascii=False)[:1000]}")
        deadline = time.time() + 900
        while time.time() < deadline:
            polled = get_json_request(f"https://api.openai.com/v1/videos/{urllib.parse.quote(video_id, safe='')}", {"Authorization": f"Bearer {api_key}"}, timeout=120)
            status = str(polled.get("status") or "").lower()
            if status in {"failed", "cancelled", "canceled"}:
                raise ToolError(f"OpenAI video generation failed: {json.dumps(polled, ensure_ascii=False)[:1200]}")
            if status in {"completed", "succeeded", "success"}:
                video_url = first_url(polled)
                break
            time.sleep(5)
        if video_url:
            download_binary(video_url, output_path, {"Authorization": f"Bearer {api_key}"})
        else:
            download_binary(f"https://api.openai.com/v1/videos/{urllib.parse.quote(video_id, safe='')}/content", output_path, {"Authorization": f"Bearer {api_key}"})
    elif provider == "gemini":
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:predictLongRunning?key={urllib.parse.quote(api_key, safe='')}"
        instance: dict[str, Any] = {"prompt": prompt}
        inline = image_inline_payload(reference_images[0] if reference_images else None)
        if inline:
            instance["image"] = {"mimeType": inline["mimeType"], "bytesBase64Encoded": inline["bytesBase64Encoded"]}
        operation = post_json_request(endpoint, {"instances": [instance], "parameters": {"sampleCount": 1, "durationSeconds": seconds, "aspectRatio": "9:16"}}, {}, timeout=120)
        op_name = str(operation.get("name") or "")
        if not op_name:
            raise ToolError(f"Gemini video response did not include operation name: {json.dumps(operation, ensure_ascii=False)[:1000]}")
        poll_url = f"https://generativelanguage.googleapis.com/v1beta/{op_name}?key={urllib.parse.quote(api_key, safe='')}" if not op_name.startswith("http") else op_name
        deadline = time.time() + 900
        while time.time() < deadline:
            polled = get_json_request(poll_url, {}, timeout=120)
            failure = operation_failed(polled)
            if failure:
                raise ToolError(f"Gemini video generation failed: {failure}")
            if operation_done(polled):
                video_url = first_url(polled)
                break
            time.sleep(5)
        if not video_url:
            raise ToolError("Gemini video generation completed without a downloadable video URL")
        download_binary(video_url, output_path, {"x-goog-api-key": api_key})
    elif provider == "wan":
        input_payload: dict[str, Any] = {"prompt": prompt}
        if reference_images:
            media_type = "reference_image" if "r2v" in model else "first_frame"
            input_payload["media"] = [{"type": media_type, "url": dashscope_upload_file(api_key, model, image_path)} for image_path in reference_images[: int(video_capability(config).get("max_reference_images") or 1)]]
        started = post_json_request("https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis", {"model": model, "input": input_payload, "parameters": {"duration": seconds}}, {"Authorization": f"Bearer {api_key}", "X-DashScope-Async": "enable", "X-DashScope-OssResourceResolve": "enable"}, timeout=120)
        task_id_value = str(((started.get("output") or {}).get("task_id") or started.get("task_id") or ""))
        if not task_id_value:
            raise ToolError(f"Wan response did not include task_id: {json.dumps(started, ensure_ascii=False)[:1000]}")
        poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{urllib.parse.quote(task_id_value, safe='')}"
        deadline = time.time() + 900
        while time.time() < deadline:
            polled = get_json_request(poll_url, {"Authorization": f"Bearer {api_key}"}, timeout=120)
            payload = polled.get("output") if isinstance(polled.get("output"), dict) else polled
            failure = operation_failed(payload)
            if failure:
                raise ToolError(f"Wan video generation failed: {failure}")
            if operation_done(payload):
                video_url = first_url(polled)
                break
            time.sleep(5)
        if not video_url:
            raise ToolError("Wan video generation completed without a downloadable video URL")
        download_binary(video_url, output_path)
    else:
        raise ToolError(f"Unsupported video provider: {provider}/{model}")
    return {"provider": provider, "model": model, "duration": seconds, "output_path": str(output_path), "video_url": video_url, "elapsed_seconds": round(time.time() - started_at, 3)}


def sync_output_url(payload: dict[str, Any]) -> str:
    url = str(payload.get("outputUrl") or payload.get("output_url") or "").strip()
    if url:
        return url
    for segment in payload.get("segments") or []:
        if isinstance(segment, dict):
            candidate = str(segment.get("segmentOutputUrl") or segment.get("segment_output_url") or "").strip()
            if candidate:
                return candidate
    return first_url(payload)


def run_sync_lipsync(config: dict[str, str], video_path: Path, audio_path: Path, output_path: Path, request_path: Path, status_path: Path, create_response_path: Path) -> dict[str, Any]:
    try:
        import requests  # type: ignore
    except Exception as exc:
        raise ToolError("requests is required for Sync.so multipart lip-sync upload") from exc
    api_key = str(config.get("api_key") or "")
    model = str(config.get("model") or "lipsync-2")
    request_record = {
        "created_at": now_ms(),
        "endpoint": "https://api.sync.so/v2/generate",
        "model": model,
        "video_path": str(video_path),
        "audio_path": str(audio_path),
        "video_size_bytes": video_path.stat().st_size,
        "audio_size_bytes": audio_path.stat().st_size,
    }
    write_json(request_path, request_record)
    started_at = time.time()
    with video_path.open("rb") as video_file, audio_path.open("rb") as audio_file:
        response = requests.post(
            "https://api.sync.so/v2/generate",
            headers={"x-api-key": api_key},
            data={"model": model},
            files={
                "video": (video_path.name, video_file, "video/mp4"),
                "audio": (audio_path.name, audio_file, "audio/wav"),
            },
            timeout=120,
        )
    try:
        created = response.json()
    except ValueError:
        created = {"raw_text": response.text}
    write_json(create_response_path, {"status_code": response.status_code, "body": created})
    if response.status_code >= 400:
        raise ToolError(f"Sync.so create failed: HTTP {response.status_code}: {json.dumps(created, ensure_ascii=False)[:1200]}")
    generation_id = str(created.get("id") or "").strip()
    if not generation_id:
        raise ToolError(f"Sync.so create response did not include id: {created}")
    history: list[dict[str, Any]] = []
    final_payload: dict[str, Any] = {}
    deadline = time.time() + 30 * 60
    while time.time() < deadline:
        response = requests.get(f"https://api.sync.so/v2/generate/{generation_id}", headers={"x-api-key": api_key}, timeout=60)
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_text": response.text}
        status = str(payload.get("status") or "").upper()
        final_payload = payload
        history.append({"checked_at": now_ms(), "status_code": response.status_code, "status": status, "body": payload})
        write_json(status_path, {"generation_id": generation_id, "history": history, "latest": {"status_code": response.status_code, "body": payload}})
        if response.status_code >= 400:
            raise ToolError(f"Sync.so poll failed: HTTP {response.status_code}: {json.dumps(payload, ensure_ascii=False)[:1200]}")
        if status in {"COMPLETED", "FAILED", "REJECTED"}:
            break
        time.sleep(15)
    status = str(final_payload.get("status") or "").upper()
    if status != "COMPLETED":
        raise ToolError(f"Sync.so generation ended with {status or 'UNKNOWN'}: {json.dumps(final_payload, ensure_ascii=False)[:1200]}")
    output_url = sync_output_url(final_payload)
    if not output_url:
        raise ToolError(f"Completed Sync.so generation did not include outputUrl: {json.dumps(final_payload, ensure_ascii=False)[:1200]}")
    download = requests.get(output_url, headers={"x-api-key": api_key}, stream=True, timeout=300)
    try:
        if download.status_code in {401, 403}:
            download.close()
            download = requests.get(output_url, stream=True, timeout=300)
        download.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as out:
            for chunk in download.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    out.write(chunk)
    finally:
        download.close()
    return {"provider": config.get("provider"), "model": model, "generation_id": generation_id, "output_url": output_url, "output_path": str(output_path), "duration": ffprobe_duration(output_path), "elapsed_seconds": round(time.time() - started_at, 3)}


def render_tts_prompt_template(prompt: str, text_value: str) -> str:
    prompt_text = str(prompt or "").strip()
    current_text = str(text_value or "").strip()
    if not prompt_text:
        return ""
    if "{text}" in prompt_text:
        return prompt_text.replace("{text}", current_text)
    marker = re.search(r"(正文\s*[:：]\s*)", prompt_text)
    if marker:
        return prompt_text[: marker.end()] + current_text
    return prompt_text


def tts_sound_event_instructions(text_value: str) -> list[str]:
    instructions = []
    for raw_event in re.findall(r"[【\[]([^】\]]+)[】\]]", str(text_value or "")):
        event = one_line_text(raw_event)
        if not event:
            continue
        lower = event.lower()
        if "咳" in event or "cough" in lower:
            if "三" in event or "3" in event or "three" in lower:
                instructions.append(f"遇到【{event}】时，不要朗读括号文字；请在该位置真实地轻微模拟咳嗽三声：“咳、咳、咳”，每声短促，三声之间有很短停顿，然后继续后面的正文。")
            else:
                instructions.append(f"遇到【{event}】时，不要朗读括号文字；请在该位置自然模拟短促咳嗽声，然后继续后面的正文。")
        else:
            instructions.append(f"遇到【{event}】时，不要朗读括号文字；把它作为声音/表演动作提示自然执行，然后继续后面的正文。")
    return instructions


def apply_tts_sound_event_instructions(prompt: str, text_value: str) -> str:
    instructions = tts_sound_event_instructions(text_value)
    if not instructions:
        return prompt
    guidance = "声音动作规则：正文里的【】或[]内容是声音表演指令，不是要念出来的文字。\n" + "\n".join(f"- {item}" for item in instructions)
    prompt_text = str(prompt or "").strip()
    marker = re.search(r"(?m)^正文\s*[:：]", prompt_text)
    if marker:
        return (prompt_text[: marker.start()].rstrip() + "\n" + guidance + "\n" + prompt_text[marker.start():]).strip()
    return (prompt_text + "\n" + guidance).strip()


def tts_prompt_for_shot(shot: dict[str, Any], package: dict[str, Any], text_value: str) -> tuple[str, str, str]:
    final_tts = package.get("tts") if isinstance(package.get("tts"), dict) else {}
    plan_a = shot.get("plan_a") if isinstance(shot.get("plan_a"), dict) else {}
    selection = shot.get("tts_selection") if isinstance(shot.get("tts_selection"), dict) else {}
    prompt = render_tts_prompt_template(str(package.get("tts_prompt") or final_tts.get("execution_prompt") or plan_a.get("tts_prompt") or "").strip(), text_value)
    if not prompt:
        prompt_template = str(selection.get("prompt_template") or "").strip()
        if prompt_template:
            prompt = render_tts_prompt_template(prompt_template, text_value)
        elif str(selection.get("prompt") or "").strip():
            prompt = render_tts_prompt_template(str(selection.get("prompt") or "").strip(), text_value)
    if not prompt:
        prompt = "Natural energetic Chinese commercial narration. Read only the provided script."
    prompt = apply_tts_sound_event_instructions(prompt, text_value)
    provider = str(selection.get("provider") or "").strip()
    model = str(selection.get("model") or "").strip()
    return prompt, provider, model


def build_tts_timeline(workspace: Path, shot: dict[str, Any], audio_duration: float, normalized_entries: list[dict[str, Any]], reference_images: list[dict[str, Any]], variant_id: str) -> dict[str, Any]:
    shot_id = shot_id_of(shot)
    image_pages = []
    if reference_images:
        for index, entry in enumerate(normalized_entries):
            image = reference_images[min(index, len(reference_images) - 1)]
            image_pages.append({
                "shot_id": shot_id,
                "scene_mark_id": image.get("scene_mark_id") or "",
                "image": image.get("rel") or "",
                "text": entry.get("text") or "",
                "start": entry.get("start"),
                "end": entry.get("end"),
                "duration": entry.get("duration"),
            })
    return {
        "shot_id": shot_id,
        "duration": round(audio_duration, 3),
        "audio_source": "scene_locked_tts",
        "srt_entries": normalized_entries,
        "image_pages": image_pages,
        "timing_source": "plan_d_scene_tts_concatenation",
        "generated_at": now_ms(),
    }


def build_timed_video_prompt(shot: dict[str, Any], package: dict[str, Any], video_config: dict[str, str], normalized_entries: list[dict[str, Any]], reference_images: list[dict[str, Any]], audio_duration: float) -> tuple[str, dict[str, Any]]:
    shot_id = shot_id_of(shot)
    scenes = [item for item in (package.get("scenes") or []) if isinstance(item, dict)]
    scene_prompts = []
    for index, scene in enumerate(scenes, start=1):
        prompt = scene_video_prompt(scene)
        if prompt:
            scene_prompts.append({"index": index, "scene_mark_id": str(scene.get("scene_mark_id") or ""), "prompt": prompt})
    if not scene_prompts:
        scene_prompts.append({"index": 1, "scene_mark_id": f"{shot_id}_scene_001", "prompt": "Realistic vertical phone-camera talking-head commercial video."})
    timing_lines = []
    for entry in normalized_entries:
        timing_lines.append(f"- {safe_float(entry.get('start')):.2f}s-{safe_float(entry.get('end')):.2f}s: host says \"{entry.get('text') or ''}\"; mouth movement should match this speaking window.")
    reference_lines = []
    for index, image in enumerate(reference_images, start=1):
        reference_lines.append(f"- Reference image {index}: {image.get('scene_mark_id') or 'scene'} ({image.get('rel')})")
    prompt_lines = []
    for item in scene_prompts:
        prompt_lines.append(f"Scene {item['index']} {item['scene_mark_id']}:\n{item['prompt']}")
    provider = f"{video_config.get('provider')}/{video_config.get('model')}"
    text = f"""Create a realistic vertical 9:16 product video for Shot {shot_id}.
Target video provider/model: {provider}
Target duration: {audio_duration:.2f} seconds.

Hard requirements:
- Use the provided reference image(s) as anchors for visible people, products, room, lighting, and camera.
- For any scene whose reference image clearly shows a visible speaker's head/face, preserve that person and allow natural speaking motion. Mouth movement only needs rough timing; exact lip sync will be repaired by a separate lip-sync pass using the locked TTS audio.
- For any scene whose reference image is product-only, object-only, or does not show a head/face, do not introduce a human head, face, portrait, mouth, eyes, or upper body. Keep the image focused on product/object presentation; hands may appear only as product-operating hands when already implied by the reference.
- Keep motion subtle: natural blinking and small head movement for visible speakers, or gentle product/hand movement for product-only scenes. Avoid large camera moves, cuts, zooms, subtitles, captions, logos, watermarks, UI text, or extra on-screen text.
- Do not add generated audio that conflicts with the final TTS. If audio is generated, it will be discarded by the lip-sync step.

Reference images:
{chr(10).join(reference_lines) if reference_lines else "- No reference image available; generate from prompt only."}

Locked TTS sentence timing:
{chr(10).join(timing_lines)}

Scene visual prompts:
{chr(10).join(prompt_lines)}
""".strip()
    text, prompt_compaction = compact_timed_video_prompt(text, video_config, shot_id, provider, audio_duration, reference_lines, timing_lines, scene_prompts)
    payload = {
        "shot_id": shot_id,
        "provider": video_config.get("provider"),
        "model": video_config.get("model"),
        "duration": round(audio_duration, 3),
        "reference_images": [{"scene_mark_id": item.get("scene_mark_id"), "path": item.get("rel")} for item in reference_images],
        "srt_entries": normalized_entries,
        "scene_prompts": scene_prompts,
        "prompt": text,
        "prompt_compaction": prompt_compaction,
        "generated_at": now_ms(),
    }
    return text, payload


def update_package_with_plan_d(package: dict[str, Any], prompt_payload: dict[str, Any], prompt_text_rel: str, prompt_json_rel: str, raw_video_rel: str, final_video_rel: str) -> dict[str, Any]:
    updated = {**package}
    timestamp = now_ms()
    updated["updated_at"] = timestamp
    updated["prompt_package_version"] = updated.get("prompt_package_version") or "final_v1"
    scene_prompt_map = {
        str(item.get("scene_mark_id") or "").strip(): str(item.get("prompt") or "").strip()
        for item in (prompt_payload.get("scene_prompts") or [])
        if isinstance(item, dict) and str(item.get("scene_mark_id") or "").strip()
    }
    scenes = []
    for scene in [item for item in (updated.get("scenes") or []) if isinstance(item, dict)]:
        scene_id = str(scene.get("scene_mark_id") or "").strip()
        scene_update = {**scene}
        if scene_prompt_map.get(scene_id):
            scene_update["plan_d_video_prompt"] = scene_prompt_map[scene_id]
        scenes.append(scene_update)
    if scenes:
        updated["scenes"] = scenes
    updated["latest_video_prompt"] = {
        "source": "plan_d_scene_tts_timed_prompt",
        "path": prompt_text_rel,
        "json_path": prompt_json_rel,
        "raw_video": raw_video_rel,
        "final_video": final_video_rel,
        "provider": prompt_payload.get("provider"),
        "model": prompt_payload.get("model"),
        "duration": prompt_payload.get("duration"),
        "updated_at": timestamp,
    }
    return updated


def sync_plan_with_plan_d(shot: dict[str, Any], workspace: Path, package_path: Path, package: dict[str, Any], prompt_payload: dict[str, Any], final_video: Path, variant_id: str) -> None:
    timestamp = now_ms()
    existing = shot.get("final_prompt_package") if isinstance(shot.get("final_prompt_package"), dict) else {}
    shot["final_prompt_package"] = {
        **existing,
        "path": rel(workspace, package_path),
        "updated_at": timestamp,
        "updated_by": TOOL_ID,
        "scene_count": len([item for item in (package.get("scenes") or []) if isinstance(item, dict)]),
        "target_video_model": f"{prompt_payload.get('provider')}/{prompt_payload.get('model')}",
        "latest_video_prompt_path": prompt_payload.get("video_prompt_timed") or rel(workspace, plan_d_prompt_text_path(workspace, shot_id_of(shot), variant_id)),
    }
    shot["plan_d"] = {
        "status": "completed",
        "output_video": rel(workspace, final_video),
        "prompt": rel(workspace, plan_d_prompt_text_path(workspace, shot_id_of(shot), variant_id)),
        "prompt_json": rel(workspace, plan_d_prompt_json_path(workspace, shot_id_of(shot), variant_id)),
        "provider": prompt_payload.get("provider"),
        "model": prompt_payload.get("model"),
        "duration": prompt_payload.get("duration"),
        "updated_at": timestamp,
    }


def dependencies_for_shot(workspace: Path, shot: dict[str, Any], package: dict[str, Any], args: argparse.Namespace, database_url: str) -> dict[str, Any]:
    shot_id = shot_id_of(shot)
    missing: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    satisfied: list[str] = []
    package_path = final_prompt_package_path(workspace, shot, args.variant_id)
    if package_path.exists():
        satisfied.append("final_prompt_package")
    else:
        missing.append({"dependency": "final_prompt_package", "reason": f"missing final prompt package: {rel(workspace, package_path)}", "suggested_tools": ["05_02_Shot_FinalPromptPackageBuild"]})
    scene_segments = scene_tts_segments(workspace, shot, package, args.variant_id)
    scene_tts_errors = validate_scene_tts_segments(scene_segments)
    if scene_segments and not scene_tts_errors:
        satisfied.append("scene_tts")
    else:
        missing.append({
            "dependency": "scene_tts",
            "reason": "; ".join(scene_tts_errors) if scene_tts_errors else "shot has no scene marks with scene-level TTS",
            "suggested_tools": ["07_02_Shot_PlanA_TTSGenerateAndLock"],
        })
    refs = reference_images_for_shot(workspace, shot, args.variant_id)
    if refs:
        satisfied.append("scene_assets")
    else:
        warnings.append({"dependency": "scene_assets", "reason": f"no reference images found under Assets/{args.variant_id}/{shot_id}; video will be text-only"})
    lipsync_plan = shot_lipsync_plan(shot, package)
    for kind in ("video", "lipsync"):
        try:
            if kind == "lipsync" and not lipsync_plan.get("needs_lipsync"):
                satisfied.append("lipsync_config_skipped")
                continue
            load_active_media_config(database_url, kind)
            satisfied.append(f"{kind}_config")
        except Exception as exc:
            missing.append({"dependency": f"{kind}_config", "reason": str(exc), "suggested_tools": ["Connection Config"]})
    return {"status": "blocked" if missing else "warning" if warnings else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": warnings}


def check_dependencies(workspace: Path, plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    missing = []
    if len(getattr(args, "shot_id", []) or []) != 1:
        missing.append({"dependency": "shot_id", "reason": "PlanD requires exactly one --shot-id", "suggested_tools": []})
    database_url = resolve_database_url(args)
    results = []
    if not missing:
        for shot in target_shots(plan, args):
            package, _ = load_final_prompt_package(workspace, shot, args.variant_id)
            results.append(dependencies_for_shot(workspace, shot, package, args, database_url))
    child_missing = [item for result in results for item in (result.get("missing") or [])]
    child_warnings = [item for result in results for item in (result.get("warnings") or [])]
    return {
        "status": "blocked" if missing or child_missing else "warning" if child_warnings else "satisfied",
        "satisfied": [item for result in results for item in (result.get("satisfied") or [])],
        "missing": missing + child_missing,
        "warnings": child_warnings,
    }


def run_tool(workspace: Path, plan: dict[str, Any], source_package: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    del source_package
    database_url = resolve_database_url(args)
    video_config = load_active_media_config(database_url, "video")
    results = []
    blockers = []
    for shot in target_shots(plan, args):
        shot_id = shot_id_of(shot)
        variant_id = args.variant_id
        package, package_path = load_final_prompt_package(workspace, shot, variant_id)
        lipsync_plan = shot_lipsync_plan(shot, package)
        scene_segments = scene_tts_segments(workspace, shot, package, variant_id)
        scene_tts_errors = validate_scene_tts_segments(scene_segments)
        if scene_tts_errors:
            blockers.extend(f"{shot_id}: {item}" for item in scene_tts_errors)
            results.append({"shot_id": shot_id, "status": "blocked", "blocking_errors": scene_tts_errors})
            continue
        locked_audio = temporary_lipsync_audio_path(shot_id)
        shot_srt_path = plan_d_report_path(workspace, shot_id, "plan_d_scene_tts_timing.srt", variant_id)
        timeline_path = plan_d_report_path(workspace, shot_id, "plan_d_scene_tts_timeline.locked.json", variant_id)
        tts_manifest_path = plan_d_report_path(workspace, shot_id, "plan_d_scene_tts_manifest.json", variant_id)
        normalized_entries = scene_segments_to_srt_entries(scene_segments)
        text_value = strip_srt_timing(" ".join(str(entry.get("text") or "") for entry in normalized_entries))
        compose_result = compose_scene_tts_audio(workspace, shot_id, scene_segments, locked_audio)
        tts_result = {
            "source": "scene_locked_tts_concat",
            "output_path": str(locked_audio),
            "duration": compose_result.get("duration"),
            "expected_duration": compose_result.get("expected_duration"),
            "duration_delta": compose_result.get("duration_delta"),
        }
        tts_source = "scene_locked_tts_concat"
        audio_duration = ffprobe_duration(locked_audio)
        if not audio_duration or audio_duration <= 0:
            blockers.append(f"{shot_id}: plan_d_scene_tts_duration_unreadable")
            results.append({"shot_id": shot_id, "status": "blocked", "blocking_errors": ["plan_d_scene_tts_duration_unreadable"]})
            continue
        reference_images = reference_images_for_shot(workspace, shot, variant_id)
        write_text(shot_srt_path, srt_from_entries(normalized_entries))
        write_json(timeline_path, build_tts_timeline(workspace, shot, audio_duration, normalized_entries, reference_images, variant_id))
        write_json(tts_manifest_path, {
            "shot_id": shot_id,
            "status": "locked",
            "source": tts_source,
            "text": text_value,
            "duration": audio_duration,
            "expected_duration": compose_result.get("expected_duration"),
            "duration_delta": compose_result.get("duration_delta"),
            "temporary_lipsync_audio": str(locked_audio),
            "shot_srt": rel(workspace, shot_srt_path),
            "timeline": rel(workspace, timeline_path),
            "scene_segments": [
                {
                    "scene_mark_id": segment.get("scene_mark_id"),
                    "audio": segment.get("audio"),
                    "start": segment.get("start"),
                    "end": segment.get("end"),
                    "duration": segment.get("duration"),
                    "text": segment.get("text"),
                }
                for segment in scene_segments
            ],
            "compose": compose_result,
            "generated_at": now_ms(),
        })
        package["tts"] = {**(package.get("tts") if isinstance(package.get("tts"), dict) else {}), "source": tts_source, "text": text_value, "duration": audio_duration}
        prompt_text, prompt_payload = build_timed_video_prompt(shot, package, video_config, normalized_entries, reference_images, audio_duration)
        if args.use_existing_final_prompt:
            prompt_payload["ignored_existing_final_prompt"] = {
                "reason": "Plan D video prompts must be rebuilt from canonical scene-level TTS timing",
            }
        prompt_limit = video_prompt_limit(video_config)
        if prompt_limit and len(prompt_text) > prompt_limit:
            existing_compaction = prompt_payload.get("prompt_compaction") if isinstance(prompt_payload.get("prompt_compaction"), dict) else {}
            reference_lines = []
            for index, image in enumerate(reference_images, start=1):
                reference_lines.append(f"- Reference image {index}: {image.get('scene_mark_id') or 'scene'} ({image.get('rel')})")
            timing_lines = []
            for entry in normalized_entries:
                timing_lines.append(f"- {safe_float(entry.get('start')):.2f}s-{safe_float(entry.get('end')):.2f}s: host says \"{entry.get('text') or ''}\"; mouth movement should match this speaking window.")
            provider_label = f"{video_config.get('provider')}/{video_config.get('model')}"
            prompt_text, prompt_compaction = compact_timed_video_prompt(prompt_text, video_config, shot_id, provider_label, audio_duration, reference_lines, timing_lines, prompt_payload.get("scene_prompts") or [])
            if existing_compaction:
                prompt_compaction["previous_compaction"] = existing_compaction
            prompt_payload["prompt"] = prompt_text
            prompt_payload["prompt_compaction"] = prompt_compaction
        prompt_text_path = plan_d_prompt_text_path(workspace, shot_id, variant_id)
        prompt_json_path = plan_d_prompt_json_path(workspace, shot_id, variant_id)
        raw_video = plan_d_raw_video_path(workspace, shot_id, variant_id)
        final_video = plan_d_shot_video_path(workspace, shot_id, variant_id)
        prompt_payload["video_prompt_timed"] = rel(workspace, prompt_text_path)
        write_text(prompt_text_path, prompt_text)
        write_json(prompt_json_path, prompt_payload)
        package = update_package_with_plan_d(package, prompt_payload, rel(workspace, prompt_text_path), rel(workspace, prompt_json_path), rel(workspace, raw_video), rel(workspace, final_video))
        write_json(package_path, package)
        sync_plan_with_plan_d(shot, workspace, package_path, package, prompt_payload, final_video, variant_id)
        raw_generation = {"source": "existing_raw_video"}
        if args.force or args.force_video_refresh or not raw_video.exists() or raw_video.stat().st_size <= 0:
            raw_generation = generate_scene_raw_videos(
                workspace,
                shot_id,
                video_config,
                normalized_entries,
                reference_images,
                prompt_payload.get("scene_prompts") or [],
                raw_video,
                variant_id,
            )
        if not raw_video.exists() or raw_video.stat().st_size <= 0:
            blockers.append(f"{shot_id}: raw_video_missing")
            results.append({"shot_id": shot_id, "status": "blocked", "blocking_errors": ["raw_video_missing"]})
            continue
        lipsync_request = plan_d_report_path(workspace, shot_id, "plan_d_lipsync_request.json", variant_id)
        lipsync_status = plan_d_report_path(workspace, shot_id, "plan_d_lipsync_status.json", variant_id)
        lipsync_create = plan_d_report_path(workspace, shot_id, "plan_d_lipsync_create_response.json", variant_id)
        lipsync_config: dict[str, Any] = {}
        if lipsync_plan.get("needs_lipsync"):
            lipsync_config = load_active_media_config(database_url, "lipsync")
            lipsync_result = {"source": "existing_lipsync_video", "decision": lipsync_plan}
            if args.force or args.force_lipsync_refresh or not final_video.exists() or final_video.stat().st_size <= 0:
                lipsync_result = {
                    **run_scene_lipsync_then_concat(
                        workspace,
                        shot_id,
                        lipsync_config,
                        normalized_entries,
                        final_video,
                        variant_id,
                    ),
                    "decision": lipsync_plan,
                }
        else:
            should_mux_audio = (
                args.force
                or args.force_video_refresh
                or args.force_lipsync_refresh
                or not final_video.exists()
                or final_video.stat().st_size <= 0
                or (raw_video.exists() and final_video.exists() and raw_video.stat().st_mtime > final_video.stat().st_mtime)
            )
            voiceover_mux: dict[str, Any] | None = None
            if should_mux_audio:
                voiceover_mux = mux_video_with_audio(workspace, raw_video, locked_audio, final_video)
            lipsync_result = {"source": "skipped_no_lipsync_task", "skipped": True, "decision": lipsync_plan, "output_path": str(final_video)}
            if voiceover_mux:
                lipsync_result["voiceover_mux"] = voiceover_mux
        if not final_video.exists() or final_video.stat().st_size <= 0:
            blockers.append(f"{shot_id}: final_video_missing")
            results.append({"shot_id": shot_id, "status": "blocked", "blocking_errors": ["final_video_missing"], "output_video": rel(workspace, final_video)})
            continue
        mode = "plan_d_tts_driven_active_video_lipsync" if lipsync_plan.get("needs_lipsync") else "plan_d_tts_driven_active_video_no_lipsync"
        manifest = {
            "shot_id": shot_id,
            "status": "completed",
            "mode": mode,
            "output_video": rel(workspace, final_video),
            "raw_video": rel(workspace, raw_video),
            "locked_tts": "scene_locked_tts",
            "temporary_lipsync_audio": str(locked_audio),
            "shot_srt": rel(workspace, shot_srt_path),
            "tts_timeline": rel(workspace, timeline_path),
            "video_prompt_timed": rel(workspace, prompt_text_path),
            "video_prompt_timed_json": rel(workspace, prompt_json_path),
            "final_prompt_package": rel(workspace, package_path),
            "tts": {
                "source": tts_source,
                "duration": audio_duration,
                "expected_duration": tts_result.get("expected_duration"),
                "duration_delta": tts_result.get("duration_delta"),
                "scene_segments": [
                    {
                        "scene_mark_id": segment.get("scene_mark_id"),
                        "audio": segment.get("audio"),
                        "start": segment.get("start"),
                        "end": segment.get("end"),
                        "duration": segment.get("duration"),
                    }
                    for segment in scene_segments
                ],
            },
            "video": {**raw_generation, "config": redact_config(video_config), "duration": ffprobe_duration(raw_video)},
            "lipsync": {**lipsync_result, "config": redact_config(lipsync_config)},
            "duration": ffprobe_duration(final_video),
            "stream_count": ffprobe_stream_count(final_video),
            "generated_at": now_ms(),
        }
        write_json(plan_d_report_path(workspace, shot_id, "plan_d_12_01_tts_driven_lipsync_video_generate.json", variant_id), manifest)
        results.append(manifest)
    return {"status": "completed_with_blockers" if blockers else "completed", "tool_id": TOOL_ID, "blocking_errors": blockers, "results": results}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--shot-id", action="append", default=[])
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="")
    parser.add_argument("--variant-id", default="variant_001")
    parser.add_argument("--tts-voice", default=DEFAULT_TTS_VOICE)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-prompt-refresh", action="store_true")
    parser.add_argument("--force-tts-refresh", action="store_true")
    parser.add_argument("--force-video-refresh", action="store_true")
    parser.add_argument("--force-lipsync-refresh", action="store_true")
    parser.add_argument("--use-existing-final-prompt", action="store_true")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dependencies = {"status": "unknown", "satisfied": [], "missing": [], "warnings": []}
    workspace = Path.cwd()
    try:
        task_context = load_task_context(args)
        workspace = resolve_workspace(args, task_context)
        if not args.source_package:
            args.source_package = str(task_context.get("source_package_path") or "source_package.json")
        plan = load_plan(workspace, args.input)
        validate_plan_context(plan, task_context)
        dependencies = check_dependencies(workspace, plan, args)
        if args.check_dependencies_only:
            result = None
            status = "blocked" if dependencies["missing"] else "completed_with_warnings" if dependencies["warnings"] else "completed"
        elif dependencies["missing"] and not args.force:
            result = None
            status = "blocked"
        else:
            source_package = load_source_package(workspace, args.source_package)
            result = run_tool(workspace, plan, source_package, args)
            status = str(result.get("status") or "completed")
            write_json(workspace / "reports" / "plan_d" / f"{TOOL_ID}.json", result)
            if args.output:
                write_plan_atomic(workspace / args.output, plan)
        payload = {
            "tool": TOOL_ID,
            "tool_version": TOOL_VERSION,
            "status": status,
            "workspace": str(workspace),
            "dependencies": dependencies,
            "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS,
            "suggested_next_tools": SUGGESTED_NEXT_TOOLS,
            "result": result,
        }
    except Exception as exc:
        payload = {
            "tool": TOOL_ID,
            "tool_version": TOOL_VERSION,
            "status": "failed",
            "workspace": str(workspace),
            "message": str(exc),
            "dependencies": dependencies,
            "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS,
            "suggested_next_tools": SUGGESTED_NEXT_TOOLS,
        }
    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] == "blocked":
        raise SystemExit(2)
    if payload["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
