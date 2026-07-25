from __future__ import annotations

import argparse
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
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.opencrew_paths import opencrew_data_dir

try:
    from OpenCut_V1.core.media_binaries import find_ffmpeg, media_env
except Exception:  # pragma: no cover - standalone fallback when package import path is unusual
    OPENCUT_DIR = Path(__file__).resolve().parent
    if str(OPENCUT_DIR) not in sys.path:
        sys.path.insert(0, str(OPENCUT_DIR))
    from core.media_binaries import find_ffmpeg, media_env  # type: ignore

try:
    from OpenCut_V1 import DEFAULT_DATABASE_URL_ENV, DEFAULT_OPENCREW_DATABASE_URL
except Exception:  # pragma: no cover
    DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
    DEFAULT_OPENCREW_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"

TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
try:
    from OpenCut_V1.core.runtime_secrets import resolve_secret_value
except Exception:  # pragma: no cover - keeps standalone legacy runs usable
    def resolve_secret_value(api_key_ref: str, legacy_value: str = "") -> str:
        return str(legacy_value or "").strip()

try:
    from OpenCut_V1.simplified_chinese import SimplifiedChineseError, to_simplified_chinese
except Exception:  # pragma: no cover - direct script execution fallback
    from simplified_chinese import SimplifiedChineseError, to_simplified_chinese  # type: ignore


TOOL_NAME = "02_01_AudioASR"
TOOL_VERSION = "0.1.0"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
DEFAULT_SOURCE_VIDEO_REL = f"{CONTEXT_DIR_NAME}/Video_Source.mp4"
DEFAULT_METADATA_REL = f"{CONTEXT_DIR_NAME}/Video_Metadata.json"
CONTEXT_ASR_SEGMENTS_REL = f"{CONTEXT_DIR_NAME}/ASR_Segments.json"
CONTEXT_ASR_SRT_REL = f"{CONTEXT_DIR_NAME}/ASR_Raw.srt"
CONTEXT_ASR_QUALITY_REL = f"{CONTEXT_DIR_NAME}/ASR_Quality.json"
SESSION_OUTPUT_AUDIO_REL = "SessionOutput/Audio_Reference.wav"
TOOL_DIR_NAME = "S3_02_01_AudioASR"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_METADATA_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Video_Metadata.json"
WORKING_AUDIO_REL = f"{TOOL_DIR_NAME}/Working/Audio_Reference.wav"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
OUTPUT_SEGMENTS_REL = f"{TOOL_DIR_NAME}/Output/ASR_Segments.json"
OUTPUT_SRT_REL = f"{TOOL_DIR_NAME}/Output/ASR_Raw.srt"
OUTPUT_QUALITY_REL = f"{TOOL_DIR_NAME}/Output/ASR_Quality.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
DEFAULT_ASR_CONFIG_TABLE = "tool_asr_provider_configs"
DEFAULT_ASR_CONFIG_NAME = "default_asr_provider"
LEGACY_DEFAULT_ASR_CONFIG_NAMES = {"aliyun_fun_asr_default", "local_whisper_default"}
DEFAULT_CLOUD_API_URL = "dashscope://audio/asr/transcription"
RECORDING_FILE_ASR_MODELS = {"fun-asr", "fun-asr-2025-08-25"}
SECRET_PATTERNS = (
    "postgresql://",
    "postgresql+psycopg://",
    "password",
    "api_key_ciphertext",
    "bearer ",
    "access_token",
    "refresh_token",
    "cookie",
)
OPENCREW_HOME = opencrew_data_dir()


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
    workspace: str
    asr_mode: str
    provider: str
    model: str
    language: str
    config_name: str
    database_url: str
    allow_local_fallback: bool
    force: bool
    resume: bool
    print_json: bool


@dataclass(frozen=True)
class ASRConfig:
    config_name: str
    provider: str
    model: str
    language: str
    api_url: str
    api_key: str
    api_key_ref: str
    extra_json: dict[str, Any]
    source: str

    def public(self) -> dict[str, Any]:
        return {
            "config_name": self.config_name,
            "provider": self.provider,
            "model": self.model,
            "language": self.language,
            "api_url": self.api_url,
            "api_key_ref": self.api_key_ref,
            "has_api_key": bool(self.api_key),
            "source": self.source,
        }


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def relpath(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def load_json_text(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        payload = json.loads(decode_text(value))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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
    if "does not exist" in text or "undefinedtable" in text:
        return "asr_config_table_missing"
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
            raise DatabaseError("database_driver_missing", "PostgreSQL driver is not available.") from exc
        try:
            conn = psycopg2.connect(normalized_url, connect_timeout=5)
            conn.set_client_encoding("UTF8")
            return conn
        except Exception as exc:
            raise DatabaseError(classify_database_exception(exc), str(exc)) from exc
    except Exception as exc:
        raise DatabaseError(classify_database_exception(exc), str(exc)) from exc


def column_name(item: Any) -> str:
    return str(getattr(item, "name", item[0] if item else ""))


def fetch_asr_config_from_db(database_url: str, config_name: str) -> ASRConfig:
    if config_name in LEGACY_DEFAULT_ASR_CONFIG_NAMES:
        config_name = DEFAULT_ASR_CONFIG_NAME
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            if config_name and config_name != DEFAULT_ASR_CONFIG_NAME:
                cursor.execute(
                    f"""
SELECT name, provider, model, language, api_url, api_key_ciphertext, api_key_ref, extra_json
FROM {DEFAULT_ASR_CONFIG_TABLE}
WHERE name = %s AND enabled = true
LIMIT 1
""",
                    (config_name,),
                )
            else:
                cursor.execute(
                    f"""
SELECT name, provider, model, language, api_url, api_key_ciphertext, api_key_ref, extra_json
FROM {DEFAULT_ASR_CONFIG_TABLE}
WHERE enabled = true
ORDER BY (name = %s) DESC, priority ASC, id ASC
LIMIT 1
""",
                    (DEFAULT_ASR_CONFIG_NAME,),
                )
            row = cursor.fetchone()
            columns = [column_name(item) for item in (cursor.description or [])]
    except Exception as exc:
        raise DatabaseError(classify_database_exception(exc), str(exc)) from exc
    finally:
        conn.close()

    if not row:
        suffix = f" for {config_name}" if config_name else ""
        raise BlockedError("asr_default_config_missing", f"No enabled ASR provider config found{suffix}.")
    data = dict(zip(columns, row))
    provider = decode_text(data.get("provider")).strip() or "local_whisper"
    model = decode_text(data.get("model")).strip() or ("small" if provider == "local_whisper" else "fun-asr")
    api_key_ref = decode_text(data.get("api_key_ref")).strip()
    legacy_key = decode_text(data.get("api_key_ciphertext")).strip()
    return ASRConfig(
        config_name=DEFAULT_ASR_CONFIG_NAME if not config_name or config_name == DEFAULT_ASR_CONFIG_NAME else decode_text(data.get("name")).strip() or config_name,
        provider=provider,
        model=model,
        language=decode_text(data.get("language")).strip() or "zh",
        api_url=decode_text(data.get("api_url")).strip() or ("" if provider == "local_whisper" else DEFAULT_CLOUD_API_URL),
        api_key=resolve_secret_value(api_key_ref, legacy_key),
        api_key_ref=api_key_ref,
        extra_json=load_json_text(data.get("extra_json")),
        source=f"postgres:{DEFAULT_ASR_CONFIG_TABLE}",
    )


def local_config(model: str = "", language: str = "") -> ASRConfig:
    return ASRConfig(
        config_name="local_whisper_runtime",
        provider="local_whisper",
        model=model or "small",
        language=language or "zh",
        api_url="",
        api_key="",
        api_key_ref="",
        extra_json={},
        source="runtime_local",
    )


def resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser() if raw_workspace else Path.cwd()
    try:
        return workspace.resolve()
    except Exception:
        return workspace.absolute()


def validate_workspace(workspace: Path) -> None:
    if not workspace.exists():
        raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise BlockedError("workspace_not_directory", f"Workspace is not a directory: {workspace}")


def load_variables(workspace: Path) -> dict[str, Any]:
    path = workspace / VARIABLES_REL
    if not path.exists():
        raise BlockedError(
            "variables_missing",
            f"Required SessionContext file is missing: {VARIABLES_REL}. 请先运行 00_PrepareSessionVariables.py 准备 Task 会话变量。",
        )
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    return payload


def load_video_metadata(workspace: Path, variables: dict[str, Any]) -> dict[str, Any]:
    raw = str(variables.get("video_metadata_path") or DEFAULT_METADATA_REL).strip()
    path = Path(raw)
    if path.is_absolute():
        raise BlockedError("video_metadata_path_not_relative", "video_metadata_path must be workspace-relative.")
    path = workspace / path
    if not path.exists():
        raise BlockedError(
            "video_metadata_missing",
            f"Required video metadata is missing: {raw}. 请先运行 01_VideoProbeMetadata.py 生成 Video_Metadata.json。",
        )
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("video_metadata_invalid", f"{raw} must contain a JSON object.")
    if float(payload.get("duration_seconds") or 0.0) <= 0:
        raise BlockedError("video_duration_missing", "Video metadata must contain duration_seconds.")
    if payload.get("has_audio") is False:
        raise BlockedError("video_has_no_audio", "Video metadata says the source video has no audio track.")
    return payload


def resolve_source_video(workspace: Path, variables: dict[str, Any]) -> Path:
    raw = str(variables.get("source_video_path") or DEFAULT_SOURCE_VIDEO_REL).strip()
    path = Path(raw)
    if path.is_absolute():
        raise BlockedError("source_video_path_not_relative", "source_video_path must be workspace-relative.")
    path = workspace / path
    if not path.exists():
        raise BlockedError(
            "source_video_missing",
            f"Source video is missing: {raw}. 请先运行 00_PrepareSessionVariables.py 复制 Video_Source.mp4。",
        )
    if not path.is_file():
        raise BlockedError("source_video_not_file", f"Source video is not a file: {raw}")
    return path


def ensure_tool_dirs(workspace: Path) -> None:
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        f"{TOOL_DIR_NAME}/Report",
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def source_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "fingerprint": hashlib.sha256(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")).hexdigest(),
    }


def config_signature(config: dict[str, Any], args: Args) -> str:
    payload = {
        "asr_mode": args.asr_mode,
        "provider": args.provider or config.get("provider"),
        "model": args.model or config.get("model"),
        "language": args.language or config.get("language"),
        "api_url": config.get("api_url"),
        "config_name": args.config_name or config.get("config_name"),
        "allow_local_fallback": bool(args.allow_local_fallback),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, env=media_env())


def extract_reference_audio(source_video: Path, output_audio: Path, sample_rate: int) -> None:
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    result = run_command([
        find_ffmpeg(),
        "-y",
        "-i",
        str(source_video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        str(output_audio),
    ])
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "ffmpeg audio extraction failed").strip()
        raise BlockedError("audio_extraction_failed", message)
    if not output_audio.exists() or output_audio.stat().st_size == 0:
        raise BlockedError("audio_extraction_empty", "Audio extraction produced an empty file.")


def sample_rate_for_config(config: ASRConfig) -> int:
    if config.provider == "aliyun_bailian_fun_asr" and "flash-8k" in config.model:
        return 8000
    return 16000


def ensure_ffmpeg_on_path(audio_path: Path) -> None:
    ffmpeg_executable = Path(find_ffmpeg())
    if shutil.which("ffmpeg") is None:
        shim_dir = audio_path.parent / ".tool_bin"
        shim_dir.mkdir(parents=True, exist_ok=True)
        shim_path = shim_dir / "ffmpeg"
        if not shim_path.exists():
            try:
                shim_path.symlink_to(ffmpeg_executable)
            except Exception:
                shutil.copyfile(ffmpeg_executable, shim_path)
                shim_path.chmod(0o755)
        os.environ["PATH"] = str(shim_dir) + os.pathsep + os.environ.get("PATH", "")
    else:
        os.environ["PATH"] = str(ffmpeg_executable.parent) + os.pathsep + os.environ.get("PATH", "")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def module_importable(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:
        return False


def python_can_import(python: Path, module: str) -> bool:
    if not python.exists():
        return False
    try:
        proc = subprocess.run([str(python), "-c", f"import {module}"], check=False, capture_output=True, text=True, timeout=20)
    except Exception:
        return False
    return proc.returncode == 0


def opencut_v1_runtime_python_candidates() -> list[Path]:
    candidates: list[Path | None] = [
        Path(os.environ["OPENCREW_OPENCUT_V1_PYTHON"]).expanduser() if os.environ.get("OPENCREW_OPENCUT_V1_PYTHON") else None,
        Path(os.environ["OPENCREW_OPENCUT_V1_RUNTIME_DIR"]).expanduser() / "bin" / "python" if os.environ.get("OPENCREW_OPENCUT_V1_RUNTIME_DIR") else None,
        opencrew_data_dir() / "runtimes" / "opencut_v1_py312" / "bin" / "python",
        Path.home().expanduser() / ".opencrew" / "runtimes" / "opencut_v1_py312" / "bin" / "python",
        repo_root() / "backend" / ".venv" / "bin" / "python",
    ]
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        if candidate is None:
            continue
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def same_python_launcher(left: Path, right: Path) -> bool:
    return left.expanduser().absolute() == right.expanduser().absolute()


def maybe_reexec_with_local_whisper_runtime() -> None:
    env_flag = "OPENCUT_V1_LOCAL_WHISPER_RUNTIME_REEXEC"
    if os.environ.get(env_flag) == "1":
        return
    python = next((candidate for candidate in opencut_v1_runtime_python_candidates() if python_can_import(candidate, "whisper")), None)
    if python is None:
        return
    if same_python_launcher(python, Path(sys.executable)):
        return
    os.environ[env_flag] = "1"
    os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])


def transcribe_local_whisper(audio_path: Path, config: ASRConfig) -> dict[str, Any]:
    maybe_reexec_with_local_whisper_runtime()
    try:
        import whisper  # type: ignore
    except Exception as exc:
        maybe_reexec_with_local_whisper_runtime()
        raise BlockedError(
            "local_whisper_dependency_missing",
            "Python package 'whisper' is not available, and the tool could not switch to the existing OpenCrew backend runtime.",
        ) from exc
    ensure_ffmpeg_on_path(audio_path)
    model = whisper.load_model(config.model)
    kwargs: dict[str, Any] = {"verbose": False}
    if config.language:
        kwargs["language"] = config.language
    payload = model.transcribe(str(audio_path), **kwargs)
    return normalize_provider_payload(payload, config)


def request_json(url: str, headers: dict[str, str], timeout: int = 60) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = res.read().decode("utf-8", errors="replace")
    return json.loads(body) if body else {}


def dashscope_upload_file(api_key: str, model: str, path: Path) -> str:
    query = urllib.parse.urlencode({"action": "getPolicy", "model": model})
    policy = request_json(
        f"https://dashscope.aliyuncs.com/api/v1/uploads?{query}",
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    policy_data = policy.get("data") if isinstance(policy.get("data"), dict) else {}
    upload_host = str(policy_data.get("upload_host") or "")
    upload_dir = str(policy_data.get("upload_dir") or "")
    if not upload_host or not upload_dir:
        raise RuntimeError("DashScope upload policy is missing upload_host/upload_dir.")
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
    with urllib.request.urlopen(req, timeout=180) as res:
        if res.status != 200:
            raise RuntimeError(f"DashScope upload failed: HTTP {res.status}")
    return f"oss://{key}"


def attr_or_get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def transcribe_dashscope_transcription(audio_path: Path, config: ASRConfig, duration: float) -> dict[str, Any]:
    if not config.api_key:
        raise BlockedError(
            "cloud_asr_api_key_missing",
            f"Cloud ASR config {config.config_name} is missing API Key. 请在 OpenCrew ModelConfig 的 ASR 设置中保存 API Key。",
        )
    try:
        from dashscope.audio.asr import Transcription  # type: ignore
    except Exception as exc:
        raise BlockedError(
            "dashscope_dependency_missing",
            "Python package 'dashscope' is not available. 请在 OpenCrew 运行环境安装 dashscope，或改用 --asr-mode local。",
        ) from exc
    file_url = dashscope_upload_file(config.api_key, config.model, audio_path)
    kwargs: dict[str, Any] = {}
    if config.language in {"zh", "cn", "zh-cn"}:
        kwargs["language_hints"] = ["zh"]
    task_response = Transcription.async_call(model=config.model, file_urls=[file_url], api_key=config.api_key, **kwargs)
    status_code = attr_or_get(task_response, "status_code")
    if status_code != HTTPStatus.OK and str(status_code) not in {"200", "HTTPStatus.OK"}:
        raise RuntimeError(f"DashScope Transcription submit failed: {status_code}")
    output = attr_or_get(task_response, "output", {})
    task_id = attr_or_get(output, "task_id", "")
    if not task_id:
        raise RuntimeError("DashScope Transcription submit did not return task_id.")
    if not hasattr(Transcription, "wait"):
        raise RuntimeError("DashScope Transcription.wait is unavailable in this SDK version.")
    final_response = Transcription.wait(task=task_id, api_key=config.api_key)
    final_output = attr_or_get(final_response, "output", {})
    results = attr_or_get(final_output, "results", []) or []
    transcription_payload: dict[str, Any] = {}
    for item in results:
        url = attr_or_get(item, "transcription_url", "") or attr_or_get(item, "url", "")
        if url:
            transcription_payload = request_json(str(url), {}, timeout=120)
            break
    if not transcription_payload:
        transcription_payload = final_output if isinstance(final_output, dict) else {}
    return normalize_dashscope_transcription_payload(transcription_payload, config, duration)


def transcribe_dashscope_recognition(audio_path: Path, config: ASRConfig) -> dict[str, Any]:
    if not config.api_key:
        raise BlockedError(
            "cloud_asr_api_key_missing",
            f"Cloud ASR config {config.config_name} is missing API Key. 请在 OpenCrew ModelConfig 的 ASR 设置中保存 API Key。",
        )
    try:
        from dashscope.audio.asr import Recognition  # type: ignore
    except Exception as exc:
        raise BlockedError(
            "dashscope_dependency_missing",
            "Python package 'dashscope' is not available. 请在 OpenCrew 运行环境安装 dashscope，或改用 --asr-mode local。",
        ) from exc
    sample_rate = sample_rate_for_config(config)
    recognition_kwargs: dict[str, Any] = {
        "timestamp_alignment_enabled": True,
        "semantic_punctuation_enabled": True,
        "multi_threshold_mode_enabled": True,
    }
    if config.language in {"zh", "cn", "zh-cn"}:
        recognition_kwargs["language_hints"] = ["zh"]
    recognition = Recognition(model=config.model, format="wav", sample_rate=sample_rate, callback=None, **recognition_kwargs)
    result = recognition.call(str(audio_path), api_key=config.api_key)
    status_code = attr_or_get(result, "status_code")
    if str(status_code) not in {"200", "HTTPStatus.OK"} and status_code != HTTPStatus.OK:
        code = attr_or_get(result, "code", "")
        message = attr_or_get(result, "message", "")
        raise RuntimeError(f"DashScope Recognition failed: {status_code} {code} {message}".strip())
    return normalize_dashscope_recognition_payload(result, config)


def transcribe_cloud_aliyun(audio_path: Path, config: ASRConfig, duration: float) -> dict[str, Any]:
    api_url = config.api_url or DEFAULT_CLOUD_API_URL
    if "transcription" in api_url or config.model in RECORDING_FILE_ASR_MODELS:
        return transcribe_dashscope_transcription(audio_path, config, duration)
    return transcribe_dashscope_recognition(audio_path, config)


def normalize_provider_payload(payload: dict[str, Any], config: ASRConfig) -> dict[str, Any]:
    segments = []
    for item in payload.get("segments") or []:
        text = decode_text(item.get("text")).strip()
        if not text:
            continue
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or start)
        segments.append({"index": len(segments) + 1, "start": start, "end": end, "text": text, "confidence": item.get("confidence"), "source": "asr"})
    return {"provider": config.provider, "model": config.model, "language": decode_text(payload.get("language")).strip() or config.language, "text": decode_text(payload.get("text")).strip(), "segments": segments}


def recursive_sentences(payload: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("sentences", "sentence"):
            value = payload.get(key)
            if isinstance(value, list):
                found.extend([item for item in value if isinstance(item, dict)])
        for value in payload.values():
            if isinstance(value, (dict, list)):
                found.extend(recursive_sentences(value))
    elif isinstance(payload, list):
        for value in payload:
            found.extend(recursive_sentences(value))
    return found


SENTENCE_END_PUNCTUATION = set("。！？!?；;，,")


def seconds_from_millis(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return number / 1000.0 if number > 1000 else number


def normalize_asr_words(words: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(words, list):
        return normalized
    for word in words:
        if not isinstance(word, dict):
            continue
        text = decode_text(word.get("text")).strip()
        punctuation = decode_text(word.get("punctuation")).strip()
        if not text and not punctuation:
            continue
        begin = word.get("begin_time", word.get("start_time", word.get("start", 0)))
        finish = word.get("end_time", word.get("stop_time", word.get("end", begin)))
        try:
            start = float(begin or 0.0) / 1000.0
            end = float(finish or begin or 0.0) / 1000.0
        except (TypeError, ValueError):
            start = 0.0
            end = 0.0
        if end < start:
            end = start
        normalized.append({"text": text, "punctuation": punctuation, "start": start, "end": end})
    return normalized


def word_text(word: dict[str, Any]) -> str:
    return f"{decode_text(word.get('text')).strip()}{decode_text(word.get('punctuation')).strip()}"


def word_ends_sentence(word: dict[str, Any]) -> bool:
    text = word_text(word)
    return bool(text) and text[-1] in SENTENCE_END_PUNCTUATION


def sentence_segments_from_words(item: dict[str, Any]) -> list[dict[str, Any]]:
    words = normalize_asr_words(item.get("words"))
    if not words:
        return []
    segments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        current.append(word)
        if word_ends_sentence(word):
            text = "".join(word_text(part) for part in current).strip()
            if text:
                segments.append({
                    "start": float(current[0]["start"]),
                    "end": float(current[-1]["end"]),
                    "text": text,
                    "confidence": item.get("confidence"),
                    "source": "asr_word_timestamps",
                    "time_source": "provider_words",
                    "words": current,
                })
            current = []
    if current:
        text = "".join(word_text(part) for part in current).strip()
        if text:
            segments.append({
                "start": float(current[0]["start"]),
                "end": float(current[-1]["end"]),
                "text": text,
                "confidence": item.get("confidence"),
                "source": "asr_word_timestamps",
                "time_source": "provider_words",
                "words": current,
            })
    return segments


def normalize_dashscope_transcription_payload(payload: dict[str, Any], config: ASRConfig, duration: float) -> dict[str, Any]:
    segments = []
    for item in recursive_sentences(payload):
        word_segments = sentence_segments_from_words(item)
        if word_segments:
            for segment in word_segments:
                segment["index"] = len(segments) + 1
                segments.append(segment)
            continue
        text = decode_text(item.get("text")).strip()
        if not text:
            continue
        begin = item.get("begin_time", item.get("start_time", item.get("start", 0)))
        finish = item.get("end_time", item.get("stop_time", item.get("end", begin)))
        start = float(begin or 0.0)
        end = float(finish or start)
        if start > 1000 or end > 1000:
            start /= 1000.0
            end /= 1000.0
        segments.append({"index": len(segments) + 1, "start": start, "end": end, "text": text, "confidence": item.get("confidence"), "source": "asr", "time_source": "provider_sentence"})
    if not segments:
        text = decode_text(payload.get("text") or payload.get("transcript")).strip()
        if text:
            segments.append({"index": 1, "start": 0.0, "end": duration, "text": text, "confidence": None, "source": "asr"})
    return {"provider": config.provider, "model": config.model, "language": config.language, "text": "".join(str(item["text"]) for item in segments), "segments": segments}


def normalize_dashscope_recognition_payload(payload: Any, config: ASRConfig) -> dict[str, Any]:
    sentences = []
    if hasattr(payload, "get_sentence"):
        sentences = payload.get_sentence() or []
    elif isinstance(payload, dict):
        sentences = ((payload.get("output") or {}).get("sentence") or [])
    segments = []
    for item in sentences:
        word_segments = sentence_segments_from_words(item)
        if word_segments:
            for segment in word_segments:
                segment["index"] = len(segments) + 1
                segments.append(segment)
            continue
        text = decode_text(item.get("text")).strip()
        if not text:
            continue
        start = float(item.get("begin_time") or 0.0) / 1000.0
        end = float(item.get("end_time") or item.get("current_time") or item.get("begin_time") or 0.0) / 1000.0
        segments.append({"index": len(segments) + 1, "start": start, "end": end, "text": text, "confidence": item.get("confidence"), "source": "asr", "time_source": "provider_sentence"})
    return {"provider": config.provider, "model": config.model, "language": config.language, "text": "".join(str(item["text"]) for item in segments), "segments": segments}


def transcribe_with_config(audio_path: Path, config: ASRConfig, duration: float) -> dict[str, Any]:
    if config.provider == "local_whisper":
        return transcribe_local_whisper(audio_path, config)
    if config.provider == "aliyun_bailian_fun_asr":
        return transcribe_cloud_aliyun(audio_path, config, duration)
    raise RuntimeError(f"Unsupported ASR provider: {config.provider}")


def segment_similarity(a: str, b: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, a, b).ratio()


def is_duplicate(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    overlap = min(float(existing["end"]), float(candidate["end"])) - max(float(existing["start"]), float(candidate["start"]))
    if overlap <= 0:
        return False
    shorter = min(float(existing["end"]) - float(existing["start"]), float(candidate["end"]) - float(candidate["start"]))
    return shorter > 0 and overlap / shorter >= 0.35 and segment_similarity(str(existing.get("text") or ""), str(candidate.get("text") or "")) >= 0.72


def normalize_segments(raw_segments: list[dict[str, Any]], duration: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    cleaned: list[dict[str, Any]] = []
    for raw in sorted(raw_segments, key=lambda item: (float(item.get("start") or 0.0), float(item.get("end") or 0.0))):
        text = decode_text(raw.get("text")).strip()
        if not text:
            warnings.append({"code": "empty_asr_text_dropped", "message": "Dropped an ASR item with empty text."})
            continue
        start = max(0.0, float(raw.get("start") or 0.0))
        end = max(start, float(raw.get("end") or start))
        if duration > 0:
            if start > duration:
                warnings.append({"code": "out_of_bounds_asr_item_dropped", "message": f"Dropped ASR item starting after video end: {start:.3f}"})
                continue
            clamped_end = min(end, duration)
            if clamped_end != end:
                warnings.append({"code": "asr_end_clamped_to_video_duration", "message": f"Clamped ASR end {end:.3f} to video duration {duration:.3f}."})
            end = clamped_end
        if end <= start:
            end = min(duration, start + 0.001) if duration > start else start
        candidate = {"index": 0, "start": round(start, 3), "end": round(end, 3), "duration": round(max(0.0, end - start), 3), "text": text, "confidence": raw.get("confidence"), "source": raw.get("source") or "asr"}
        if raw.get("time_source"):
            candidate["time_source"] = raw.get("time_source")
        if raw.get("words"):
            candidate["words"] = raw.get("words")
        duplicate_index = next((idx for idx, item in enumerate(cleaned) if is_duplicate(item, candidate)), -1)
        if duplicate_index >= 0:
            existing = cleaned[duplicate_index]
            chosen = candidate if len(candidate["text"]) > len(str(existing.get("text") or "")) else existing
            cleaned[duplicate_index] = chosen
            warnings.append({"code": "duplicate_asr_overlap_removed", "message": "Removed an overlapping duplicate ASR item."})
            continue
        cleaned.append(candidate)
    previous_end = 0.0
    for item in cleaned:
        start = float(item["start"])
        end = float(item["end"])
        if start < previous_end:
            warnings.append({"code": "asr_overlap_start_adjusted", "message": f"Adjusted overlapping ASR start {start:.3f} to {previous_end:.3f}."})
            start = previous_end
            if end < start:
                end = start
            item["start"] = round(start, 3)
            item["end"] = round(end, 3)
            item["duration"] = round(max(0.0, end - start), 3)
        previous_end = max(previous_end, float(item["end"]))
    for index, item in enumerate(cleaned, start=1):
        item["index"] = index
    return cleaned, warnings


def normalize_asr_segments_to_simplified(segments: list[dict[str, Any]]) -> int:
    changed = 0
    for item in segments:
        before = decode_text(item.get("text")).strip()
        if before:
            after = to_simplified_chinese(before)
            if after != before:
                item["text"] = after
                changed += 1
        words = item.get("words")
        if not isinstance(words, list):
            continue
        for word in words:
            if not isinstance(word, dict):
                continue
            word_before = decode_text(word.get("text")).strip()
            if not word_before:
                continue
            word_after = to_simplified_chinese(word_before)
            if word_after != word_before:
                word["text"] = word_after
                changed += 1
    return changed


def normalize_asr_payload_to_simplified(payload: dict[str, Any]) -> int:
    segments = payload.get("segments")
    if not isinstance(segments, list):
        segments = []
    changed = normalize_asr_segments_to_simplified([item for item in segments if isinstance(item, dict)])
    joined = "".join(str(item.get("text") or "") for item in segments if isinstance(item, dict))
    if joined and joined != str(payload.get("text") or ""):
        payload["text"] = joined
        changed += 1
    return changed


def record_simplified_normalization(result: dict[str, Any], changed: int) -> None:
    if changed <= 0:
        return
    result.setdefault("warnings", []).append({
        "code": "asr_text_normalized_to_simplified_chinese",
        "message": f"Normalized {changed} ASR text field(s) to Simplified Chinese.",
    })


def ensure_simplified_asr_payload(payload: dict[str, Any], result: dict[str, Any]) -> None:
    try:
        changed = normalize_asr_payload_to_simplified(payload)
    except SimplifiedChineseError as exc:
        raise BlockedError("simplified_chinese_normalizer_missing", str(exc)) from exc
    record_simplified_normalization(result, changed)


def srt_timestamp(seconds: float) -> str:
    value = max(0.0, float(seconds))
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    secs = int(value % 60)
    millis = int(round((value - int(value)) * 1000))
    if millis >= 1000:
        secs += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_srt(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for item in segments:
        text = re.sub(r"\s+", " ", decode_text(item.get("text")).strip())
        blocks.append(f"{int(item['index'])}\n{srt_timestamp(float(item['start']))} --> {srt_timestamp(float(item['end']))}\n{text}")
    return "\n\n".join(blocks).strip() + ("\n" if blocks else "")


def compute_silent_ranges(segments: list[dict[str, Any]], duration: float, min_silent_seconds: float = 1.0) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    previous = 0.0
    for item in segments:
        start = max(0.0, float(item.get("start") or 0.0))
        end = max(start, float(item.get("end") or start))
        if start - previous >= min_silent_seconds:
            ranges.append({"start": round(previous, 3), "end": round(start, 3), "duration": round(start - previous, 3)})
        previous = max(previous, end)
    if duration - previous >= min_silent_seconds:
        ranges.append({"start": round(previous, 3), "end": round(duration, 3), "duration": round(duration - previous, 3)})
    return ranges


def build_quality(segments: list[dict[str, Any]], duration: float, provider_payload: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    covered = sum(max(0.0, float(item["end"]) - float(item["start"])) for item in segments)
    text = "".join(str(item.get("text") or "") for item in segments)
    gaps = compute_silent_ranges(segments, duration)
    long_gaps = [item for item in gaps if float(item["duration"]) >= 8.0]
    quality_warnings = list(warnings)
    if not segments:
        quality_warnings.append({"code": "asr_returned_no_segments", "message": "ASR returned no usable segments."})
    if long_gaps:
        quality_warnings.append({"code": "long_asr_gap_detected", "message": "ASR contains long timestamp coverage gaps."})
    coverage = covered / duration if duration > 0 else 0.0
    if not segments or not text.strip():
        quality_level = "failed"
    elif coverage < 0.08 or len(text) < 12:
        quality_level = "weak"
    elif coverage < 0.25 or long_gaps:
        quality_level = "usable"
    else:
        quality_level = "good"
    return {
        "schema_version": "opencut_v1_asr_quality_0.1",
        "status": "checked",
        "provider": provider_payload.get("provider") or "unknown",
        "model": provider_payload.get("model") or "unknown",
        "language": provider_payload.get("language") or "unknown",
        "segment_count": len(segments),
        "text_chars": len(text),
        "video_duration_seconds": round(duration, 3),
        "covered_speech_seconds": round(covered, 3),
        "speech_coverage_ratio": round(coverage, 4),
        "silent_ranges": gaps,
        "long_asr_gap_count": len(long_gaps),
        "max_asr_gap_seconds": round(max([float(item["duration"]) for item in gaps] + [0.0]), 3),
        "timeline_alignment": {
            "status": "passed" if all(float(item["start"]) >= 0 and float(item["end"]) >= float(item["start"]) and float(item["end"]) <= duration + 0.001 for item in segments) else "failed",
            "source": "video_metadata.duration_seconds",
            "video_duration_seconds": round(duration, 3),
        },
        "quality_level": quality_level,
        "warnings": quality_warnings,
    }


def update_asr_pointers(workspace: Path) -> None:
    variables = read_json(workspace / VARIABLES_REL)
    if not isinstance(variables, dict):
        raise BlockedError("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    variables["asr_segments_path"] = CONTEXT_ASR_SEGMENTS_REL
    variables["asr_srt_path"] = CONTEXT_ASR_SRT_REL
    variables["asr_quality_path"] = CONTEXT_ASR_QUALITY_REL
    variables["updated_at"] = now_iso()
    write_json(workspace / VARIABLES_REL, variables)


def remove_asr_pointers(workspace: Path) -> None:
    path = workspace / VARIABLES_REL
    if not path.exists():
        return
    try:
        variables = read_json(path)
    except Exception:
        return
    if not isinstance(variables, dict):
        return
    for key in ("asr_segments_path", "asr_srt_path", "asr_quality_path"):
        variables.pop(key, None)
    variables["updated_at"] = now_iso()
    write_json(path, variables)


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    cleanup_actions = result.setdefault("cleanup_actions", [])
    tool_dir = workspace / TOOL_DIR_NAME
    if tool_dir.exists():
        remove_path(tool_dir)
        cleanup_actions.append({"path": TOOL_DIR_NAME, "action": "removed_for_force_rerun"})
    for rel in (CONTEXT_ASR_SEGMENTS_REL, CONTEXT_ASR_SRT_REL, CONTEXT_ASR_QUALITY_REL):
        path = workspace / rel
        if path.exists():
            remove_path(path)
            cleanup_actions.append({"path": rel, "action": "removed_for_force_rerun"})
    session_output_audio = workspace / SESSION_OUTPUT_AUDIO_REL
    if session_output_audio.exists():
        remove_path(session_output_audio)
        cleanup_actions.append({"path": SESSION_OUTPUT_AUDIO_REL, "action": "removed_for_force_rerun"})
    remove_asr_pointers(workspace)


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": args.asr_mode in {"default", "cloud"},
        "runtime": {
            "python_executable": sys.executable,
            "runtime_reexec": os.environ.get("OPENCUT_V1_LOCAL_WHISPER_RUNTIME_REEXEC") == "1",
        },
        "required_permissions": {
            "file_system": f"read/write workspace and {OPENCREW_HOME} session workspace when running Task-bound sessions",
            "network": "required for default/cloud mode database lookup and cloud ASR; not required for --asr-mode local after workspace is prepared",
        },
        "reads_session_context": [VARIABLES_REL, DEFAULT_METADATA_REL, DEFAULT_SOURCE_VIDEO_REL],
        "writes_session_context": [
            CONTEXT_ASR_SEGMENTS_REL,
            CONTEXT_ASR_SRT_REL,
            CONTEXT_ASR_QUALITY_REL,
            f"{VARIABLES_REL}:asr_segments_path/asr_srt_path/asr_quality_path",
        ],
        "writes_session_output": [SESSION_OUTPUT_AUDIO_REL],
        "created_files": [],
        "prepared_directories": [],
        "cleanup_actions": [],
        "inputs": {},
        "outputs": {},
        "provider_config": {},
        "fallback_used": False,
        "warnings": [],
        "blocked_reasons": [],
        "resume": bool(args.resume),
        "force": bool(args.force),
        "updated_at": now_iso(),
    }


def add_block(result: dict[str, Any], code: str, message: str) -> None:
    result["status"] = "blocked"
    result.setdefault("blocked_reasons", []).append({"code": code, "message": message})


def scan_for_sensitive_output(payload: dict[str, Any]) -> list[dict[str, str]]:
    text = json.dumps(payload, ensure_ascii=False).lower()
    warnings = []
    for pattern in SECRET_PATTERNS:
        if pattern in text:
            warnings.append({"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"})
    return warnings


def prepare_inputs(workspace: Path, variables: dict[str, Any], metadata: dict[str, Any], source_video: Path, source_info: dict[str, Any], signature: str, result: dict[str, Any]) -> dict[str, Any]:
    ensure_tool_dirs(workspace)
    for rel in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", f"{TOOL_DIR_NAME}/Report"):
        result.setdefault("prepared_directories", []).append(rel)
    write_json(workspace / WORKING_VARIABLES_REL, variables)
    write_json(workspace / WORKING_METADATA_REL, metadata)
    state = {
        "tool": TOOL_NAME,
        "status": "ready",
        "phase": "prepare",
        "source": source_info,
        "config_signature": signature,
        "inputs": {
            "variables": WORKING_VARIABLES_REL,
            "video_metadata": WORKING_METADATA_REL,
            "source_video": relpath(source_video, workspace),
        },
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["inputs"] = state["inputs"]
    return state


def load_reusable_outputs(workspace: Path, source_info: dict[str, Any], signature: str, force: bool) -> tuple[dict[str, Any], str, dict[str, Any]] | None:
    if force:
        return None
    paths = [
        workspace / OUTPUT_SEGMENTS_REL,
        workspace / OUTPUT_SRT_REL,
        workspace / OUTPUT_QUALITY_REL,
        workspace / WORKING_AUDIO_REL,
        workspace / WORKING_STATE_REL,
    ]
    if not all(path.exists() for path in paths):
        return None
    try:
        state = read_json(workspace / WORKING_STATE_REL)
        segments = read_json(workspace / OUTPUT_SEGMENTS_REL)
        srt_text = (workspace / OUTPUT_SRT_REL).read_text(encoding="utf-8")
        quality = read_json(workspace / OUTPUT_QUALITY_REL)
    except Exception:
        return None
    if state.get("status") != "completed":
        return None
    if (state.get("source") or {}).get("fingerprint") != source_info.get("fingerprint"):
        return None
    if state.get("config_signature") != signature:
        return None
    return segments, srt_text, quality


def resolve_runtime_config(args: Args, variables: dict[str, Any]) -> ASRConfig:
    if args.asr_mode == "local":
        defaults = variables.get("default_asr_config") if isinstance(variables.get("default_asr_config"), dict) else {}
        return local_config(args.model or "", args.language or decode_text(defaults.get("language")).strip() or "zh")
    default_asr_provider = decode_text(variables.get("default_asr_provider")).strip()
    database_url = (args.database_url or os.environ.get(DEFAULT_DATABASE_URL_ENV) or DEFAULT_OPENCREW_DATABASE_URL).strip()
    if not database_url:
        raise DatabaseError("missing_database_url", f"{DEFAULT_DATABASE_URL_ENV} is required for cloud/default ASR mode.")
    config_name = args.config_name or decode_text((variables.get("default_asr_config") or {}).get("config_name")).strip() or DEFAULT_ASR_CONFIG_NAME
    config = fetch_asr_config_from_db(database_url, config_name)
    provider = args.provider or default_asr_provider or config.provider
    model = args.model or config.model
    language = args.language or config.language
    if args.asr_mode == "cloud" and provider == "local_whisper":
        raise BlockedError("cloud_asr_config_not_cloud", "Cloud ASR mode requested, but the selected database config is local_whisper.")
    cloud_transfer_allowed = bool(variables.get("cloud_asr_data_transfer_allowed"))
    if provider != "local_whisper" and not cloud_transfer_allowed:
        raise BlockedError(
            "cloud_asr_data_transfer_not_authorized",
            "Default/cloud ASR will send task audio to the database-configured cloud ASR provider, but this session has not authorized that transfer. "
            "请先重新运行 00_PrepareSessionVariables.py 并添加 --allow-cloud-asr-data-transfer；02_01_AudioASR.py 不接受运行中补授权。或者显式使用 --asr-mode local。",
        )
    return ASRConfig(
        config_name=config.config_name,
        provider=provider,
        model=model,
        language=language,
        api_url=config.api_url,
        api_key=config.api_key,
        api_key_ref=config.api_key_ref,
        extra_json=config.extra_json,
        source=config.source,
    )


def run_asr(audio_path: Path, config: ASRConfig, duration: float, args: Args, result: dict[str, Any]) -> tuple[dict[str, Any], ASRConfig]:
    try:
        asr = transcribe_with_config(audio_path, config, duration)
        return asr, config
    except BlockedError as exc:
        result.setdefault("warnings", []).append({"code": "primary_asr_blocked", "message": f"{config.provider}/{config.model} blocked: {exc.message}"})
        if config.provider != "local_whisper" and args.allow_local_fallback:
            fallback = local_config(args.model if args.asr_mode == "local" else "", config.language)
            result["fallback_used"] = True
            asr = transcribe_with_config(audio_path, fallback, duration)
            return asr, fallback
        raise
    except Exception as exc:
        result.setdefault("warnings", []).append({"code": "primary_asr_failed", "message": f"{config.provider}/{config.model} failed: {exc}"})
        if config.provider != "local_whisper" and args.allow_local_fallback:
            fallback = local_config(args.model if args.asr_mode == "local" else "", config.language)
            result["fallback_used"] = True
            asr = transcribe_with_config(audio_path, fallback, duration)
            return asr, fallback
        raise


def finalize_outputs(workspace: Path, segments_payload: dict[str, Any], srt_text: str, quality: dict[str, Any], state: dict[str, Any], result: dict[str, Any], reused: bool) -> None:
    write_json(workspace / OUTPUT_SEGMENTS_REL, segments_payload)
    write_text(workspace / OUTPUT_SRT_REL, srt_text)
    write_json(workspace / OUTPUT_QUALITY_REL, quality)
    write_json(workspace / CONTEXT_ASR_SEGMENTS_REL, segments_payload)
    write_text(workspace / CONTEXT_ASR_SRT_REL, srt_text)
    write_json(workspace / CONTEXT_ASR_QUALITY_REL, quality)
    (workspace / SESSION_OUTPUT_AUDIO_REL).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(workspace / WORKING_AUDIO_REL, workspace / SESSION_OUTPUT_AUDIO_REL)
    update_asr_pointers(workspace)
    state = {
        **state,
        "status": "completed",
        "phase": "finalize",
        "outputs": {
            "segments": OUTPUT_SEGMENTS_REL,
            "srt": OUTPUT_SRT_REL,
            "quality": OUTPUT_QUALITY_REL,
            "session_output_audio": SESSION_OUTPUT_AUDIO_REL,
        },
        "reused_completed_output": reused,
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["outputs"] = {
        "segments": OUTPUT_SEGMENTS_REL,
        "srt": OUTPUT_SRT_REL,
        "quality": OUTPUT_QUALITY_REL,
        "session_context_segments": CONTEXT_ASR_SEGMENTS_REL,
        "session_context_srt": CONTEXT_ASR_SRT_REL,
        "session_context_quality": CONTEXT_ASR_QUALITY_REL,
        "session_output_audio": SESSION_OUTPUT_AUDIO_REL,
        "variables": VARIABLES_REL,
    }
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_METADATA_REL,
        WORKING_AUDIO_REL,
        WORKING_STATE_REL,
        OUTPUT_SEGMENTS_REL,
        OUTPUT_SRT_REL,
        OUTPUT_QUALITY_REL,
        CONTEXT_ASR_SEGMENTS_REL,
        CONTEXT_ASR_SRT_REL,
        CONTEXT_ASR_QUALITY_REL,
        SESSION_OUTPUT_AUDIO_REL,
        REPORT_RESULT_REL,
        VARIABLES_REL,
    ]
    if reused:
        result["warnings"].append({"code": "reused_completed_output", "message": "Existing ASR output was reused because the input fingerprint and config signature matched."})


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    try:
        validate_workspace(workspace)
        if args.force:
            force_reset(workspace, result)
        variables = load_variables(workspace)
        metadata = load_video_metadata(workspace, variables)
        source_video = resolve_source_video(workspace, variables)
        duration = float(metadata.get("duration_seconds") or 0.0)
        source_info = source_fingerprint(source_video)
        signature = config_signature(variables.get("default_asr_config") if isinstance(variables.get("default_asr_config"), dict) else {}, args)
        reusable = load_reusable_outputs(workspace, source_info, signature, args.force or not args.resume)
        state = prepare_inputs(workspace, variables, metadata, source_video, source_info, signature, result)
        if reusable is not None:
            segments_payload, srt_text, quality = reusable
            ensure_simplified_asr_payload(segments_payload, result)
            srt_text = render_srt(segments_payload.get("segments") or [])
            result["provider_config"] = {"source": "reused_completed_output"}
            finalize_outputs(workspace, segments_payload, srt_text, quality, state, result, reused=True)
        else:
            config = resolve_runtime_config(args, variables)
            result["provider_config"] = config.public()
            sample_rate = sample_rate_for_config(config)
            state = {**state, "phase": "audio_extract", "sample_rate": sample_rate, "updated_at": now_iso()}
            write_json(workspace / WORKING_STATE_REL, state)
            extract_reference_audio(source_video, workspace / WORKING_AUDIO_REL, sample_rate)
            state = {**state, "phase": "asr", "audio": WORKING_AUDIO_REL, "provider_config": config.public(), "updated_at": now_iso()}
            write_json(workspace / WORKING_STATE_REL, state)
            raw_asr, used_config = run_asr(workspace / WORKING_AUDIO_REL, config, duration, args, result)
            result["provider_config"] = used_config.public()
            segments, timeline_warnings = normalize_segments(raw_asr.get("segments") or [], duration)
            try:
                changed = normalize_asr_segments_to_simplified(segments)
            except SimplifiedChineseError as exc:
                raise BlockedError("simplified_chinese_normalizer_missing", str(exc)) from exc
            record_simplified_normalization(result, changed)
            result["warnings"].extend(timeline_warnings)
            segments_payload = {
                "schema_version": "opencut_v1_asr_segments_0.1",
                "provider": used_config.provider,
                "model": used_config.model,
                "language": raw_asr.get("language") or used_config.language,
                "source_video_path": relpath(source_video, workspace),
                "video_duration_seconds": round(duration, 3),
                "text": "".join(str(item.get("text") or "") for item in segments),
                "segments": segments,
                "created_at": now_iso(),
            }
            srt_text = render_srt(segments)
            quality = build_quality(segments, duration, segments_payload, result["warnings"])
            finalize_outputs(workspace, segments_payload, srt_text, quality, state, result, reused=False)
    except BlockedError as exc:
        add_block(result, exc.code, exc.message)
    except DatabaseError as exc:
        add_block(result, exc.code, exc.message)
    except PermissionError as exc:
        add_block(
            result,
            "workspace_permission_denied",
            f"Cannot read/write OpenCut_V1 workspace. 请授权 file_system read/write: {OPENCREW_HOME} 后重试。原始错误: {exc}",
        )
    except urllib.error.URLError as exc:
        result["status"] = "failed"
        result["warnings"].append({"code": "network_request_failed", "message": str(exc.reason)})
    except Exception as exc:
        result["status"] = "failed"
        result["warnings"].append({"code": "unexpected_error", "message": str(exc)})

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
    parser = argparse.ArgumentParser(description="Run OpenCut_V1 audio ASR and emit video-timeline-aligned SRT.")
    parser.add_argument("--workspace", default="", help="OpenCut_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--asr-mode", choices=["default", "cloud", "local"], default="default", help="default reads the database config; cloud requires a cloud provider; local forces local Whisper.")
    parser.add_argument("--provider", default="", help="Optional runtime provider override.")
    parser.add_argument("--model", default="", help="Optional runtime model override.")
    parser.add_argument("--language", default="", help="Optional runtime language override.")
    parser.add_argument("--config-name", default="", help="ASR database config name. Defaults to Variables.default_asr_config.config_name.")
    parser.add_argument("--database-url", default="", help="Explicit OpenCrew PostgreSQL URL. Never written to outputs.")
    parser.add_argument("--allow-local-fallback", action="store_true", help="Explicitly allow fallback to local Whisper when cloud ASR fails. Default/cloud mode never uses Whisper unless this flag or --asr-mode local is provided.")
    parser.add_argument("--force", action="store_true", help="Reset this tool's own outputs and rerun from a clean state.")
    parser.add_argument("--resume", action="store_true", help="Reuse completed output when the prepared input fingerprint and config signature match.")
    parser.add_argument("--print-json", action="store_true", help="Print Result.json payload to stdout.")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        asr_mode=str(ns.asr_mode),
        provider=str(ns.provider or ""),
        model=str(ns.model or ""),
        language=str(ns.language or ""),
        config_name=str(ns.config_name or ""),
        database_url=str(ns.database_url or ""),
        allow_local_fallback=bool(ns.allow_local_fallback),
        force=bool(ns.force),
        resume=bool(ns.resume),
        print_json=bool(ns.print_json),
    )


def main(argv: list[str] | None = None) -> int:
    cli_args = argv if argv is not None else sys.argv[1:]
    if "--tool-session-root" in cli_args:
        try:
            from ToolLibrary.OpenCut_V1.framework_bridge import maybe_run_framework_bridge
        except ModuleNotFoundError:
            repo_root = str(Path(__file__).resolve().parents[2])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from ToolLibrary.OpenCut_V1.framework_bridge import maybe_run_framework_bridge

        framework_exit = maybe_run_framework_bridge(cli_args, script_path=Path(__file__), tool_name=TOOL_NAME)
        if framework_exit is not None:
            return framework_exit

    args = parse_args(cli_args)
    if args.asr_mode == "local" or args.provider == "local_whisper":
        maybe_reexec_with_local_whisper_runtime()
    result = run(args)
    exit_code = 0 if result.get("status") == "completed" else 2 if result.get("status") == "blocked" else 1
    if args.print_json or exit_code != 0:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
