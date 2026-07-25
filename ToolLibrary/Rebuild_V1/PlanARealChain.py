from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


TOOL_NAME = "PlanARealChainRunner"
TOOL_VERSION = "0.1.0"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
CONFIG_TABLE = "tool_media_provider_configs"
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.opencrew_paths import opencrew_session_workspace

TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
from opencrew_runtime_secrets import apply_provider_proxy, resolve_secret_value


class ChainError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
        raise ChainError("PostgreSQL driver psycopg is not available") from exc
    return psycopg.connect(normalize_database_url(database_url))


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def fetch_rebuild_context(database_url: str, task_id: int) -> dict[str, Any]:
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
SELECT t.id, t.session_id, t.analysis_task_id, a.session_id AS analysis_session_id,
       t.final_prompt, t.run_model_provider, t.run_model_id,
       s.workspace_dir, s.opencode_session_id
FROM oc_rebuild_tasks t
JOIN sessions s ON s.id = t.session_id
LEFT JOIN openclip_tasks a ON a.id = t.analysis_task_id
WHERE t.id = %s
LIMIT 1
""",
                (task_id,),
            )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
        if not row:
            raise ChainError(f"OC-Rebuild Task #{task_id} not found")
        data = dict(zip(columns, row))
        return {
            "task_id": int(data.get("id") or 0),
            "session_id": int(data.get("session_id") or 0),
            "analysis_task_id": int(data.get("analysis_task_id") or 0) or None,
            "analysis_session_id": int(data.get("analysis_session_id") or 0) or None,
            "workspace_dir": decode_text(data.get("workspace_dir")).strip(),
            "final_prompt": decode_text(data.get("final_prompt")).strip(),
            "run_model_provider": decode_text(data.get("run_model_provider")).strip(),
            "run_model_id": decode_text(data.get("run_model_id")).strip(),
            "opencode_session_id": decode_text(data.get("opencode_session_id")).strip(),
        }
    finally:
        conn.close()


def load_active_media_config(database_url: str, kind: str) -> dict[str, str]:
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
            raise ChainError(f"No active {kind} model is configured")
        provider, model, api_key_ref, legacy_key = [decode_text(item).strip() for item in row]
        api_key = resolve_secret_value(api_key_ref, legacy_key)
        if not provider or not model or not api_key:
            raise ChainError(f"Active {kind} config is incomplete")
        apply_provider_proxy(provider)
        return {"kind": kind, "provider": provider, "model": model, "api_key": api_key}
    finally:
        conn.close()


def load_media_config(database_url: str, kind: str, provider: str, model: str = "") -> dict[str, str]:
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
SELECT provider, model, api_key_ref, api_key_ciphertext
FROM {CONFIG_TABLE}
WHERE kind = %s AND provider = %s AND enabled = TRUE
LIMIT 1
""",
                (kind, provider),
            )
            row = cursor.fetchone()
        if not row:
            raise ChainError(f"{kind} provider is not configured or enabled: {provider}")
        stored_provider, stored_model, api_key_ref, legacy_key = [decode_text(item).strip() for item in row]
        resolved_model = model.strip() or stored_model
        api_key = resolve_secret_value(api_key_ref, legacy_key)
        if not stored_provider or not resolved_model or not api_key:
            raise ChainError(f"{kind} config is incomplete: {provider}")
        apply_provider_proxy(stored_provider)
        return {"kind": kind, "provider": stored_provider or provider, "model": resolved_model, "api_key": api_key}
    finally:
        conn.close()


def redact_config(config: dict[str, str]) -> dict[str, str | int]:
    return {key: (len(str(value)) if key == "api_key" and not isinstance(value, int) else value) for key, value in config.items()}


def log_event(workspace: Path, name: str, payload: dict[str, Any]) -> None:
    safe = dict(payload)
    if isinstance(safe.get("config"), dict):
        safe["config"] = redact_config(safe["config"])
    safe["logged_at"] = now_ms()
    path = workspace / "logs" / "plan_a_real_chain" / f"{now_ms()}_{safe_name(name)}.json"
    write_json(path, safe)


def progress_event(workspace: Path, shot_id: str, step: str, status: str, payload: dict[str, Any] | None = None) -> None:
    data = {
        "shot_id": shot_id,
        "step": step,
        "status": status,
        "updated_at": now_ms(),
        **(payload or {}),
    }
    if isinstance(data.get("config"), dict):
        data["config"] = redact_config(data["config"])
    progress_dir = workspace / "logs" / "plan_a_real_chain" / "progress"
    write_json(progress_dir / f"{safe_name(shot_id)}__{safe_name(step)}.json", data)
    write_json(workspace / "logs" / "plan_a_real_chain" / "progress.json", data)
    log_event(workspace, f"{shot_id}_{step}_{status}", data)


def run_logged_step(workspace: Path, shot_id: str, step: str, payload: dict[str, Any], fn: Any) -> Any:
    progress_event(workspace, shot_id, step, "started", payload)
    started = time.time()
    try:
        result = fn()
    except Exception as exc:
        progress_event(workspace, shot_id, step, "failed", {**payload, "error": str(exc), "elapsed_seconds": round(time.time() - started, 3)})
        raise
    progress_event(workspace, shot_id, step, "completed", {**payload, "result": result, "elapsed_seconds": round(time.time() - started, 3)})
    return result


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())[:120] or "item"


def rel(workspace: Path, path: Path | str) -> str:
    candidate = Path(str(path))
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ChainError(f"POST {url} failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ChainError(f"POST {url} failed: {exc.reason}") from exc


def get_json_request(url: str, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ChainError(f"GET {url} failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ChainError(f"GET {url} failed: {exc.reason}") from exc


def post_binary_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> tuple[bytes, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "*/*", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read(), str(res.headers.get("Content-Type") or "application/octet-stream")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ChainError(f"POST {url} failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ChainError(f"POST {url} failed: {exc.reason}") from exc


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
            raise ChainError(f"Download failed: HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, ssl.SSLError, ConnectionError, TimeoutError) as exc:
            last_error = exc
            output_path.unlink(missing_ok=True)
            if attempt < 3:
                time.sleep(2 * attempt)
                continue
    raise ChainError(f"Download failed after 3 attempts: {last_error}")


def first_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("url", "video_url", "audio_url", "download_url", "uri"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
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


def image_b64_from_response(provider: str, payload: dict[str, Any]) -> str:
    if provider in {"openai", "xai"}:
        item = next((entry for entry in payload.get("data") or [] if isinstance(entry, dict) and entry.get("b64_json")), None)
        if item:
            return str(item["b64_json"])
    if provider == "gemini":
        for candidate in payload.get("candidates") or []:
            for part in (((candidate.get("content") or {}).get("parts")) or []):
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if inline.get("data"):
                    return str(inline["data"])
    raise ChainError("Image provider response did not include image data")


def dashscope_language_type(language: str) -> str:
    mapping = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean", "de": "German", "fr": "French", "ru": "Russian", "pt": "Portuguese", "es": "Spanish", "it": "Italian"}
    return mapping.get(str(language or "").strip().lower(), language or "Chinese")


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
        result = post_json_request(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
            {"model": model, "input": input_payload},
            {"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )
        audio_url = first_url(result)
        if audio_url:
            download_binary(audio_url, output_path)
        else:
            audio_data = first_audio_data(result)
            if not audio_data:
                raise ChainError(f"Qwen TTS response did not include audio url/data: {json.dumps(result, ensure_ascii=False)[:1000]}")
            output_path.write_bytes(base64.b64decode(audio_data))
        return {"provider": provider, "model": model, "voice_id": voice_id, "output_path": str(output_path), "audio_url": audio_url}
    if provider == "xai":
        body, content_type = post_binary_request("https://api.x.ai/v1/tts", {"text": text_value, "voice_id": voice_id, "language": "zh", "format": "mp3"}, {"Authorization": f"Bearer {api_key}"}, timeout=60)
        output_path.write_bytes(body)
        return {"provider": provider, "model": model, "voice_id": voice_id, "output_path": str(output_path), "content_type": content_type}
    raise ChainError(f"Unsupported TTS provider in real chain: {provider}/{model}")


def image_inline_payload(path: Path | None) -> dict[str, str] | None:
    if not path or not path.exists():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"mimeType": mime, "bytesBase64Encoded": encoded}


def generate_image(config: dict[str, str], prompt: str, output_path: Path, reference_path: Path | None = None) -> dict[str, Any]:
    provider = config["provider"]
    model = config["model"]
    api_key = config["api_key"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if provider == "openai":
        payload = post_json_request("https://api.openai.com/v1/images/generations", {"model": model, "prompt": prompt, "size": "1024x1536"}, {"Authorization": f"Bearer {api_key}"})
        image_bytes = base64.b64decode(image_b64_from_response(provider, payload))
    elif provider == "xai":
        payload = post_json_request("https://api.x.ai/v1/images/generations", {"model": model, "prompt": prompt, "response_format": "b64_json"}, {"Authorization": f"Bearer {api_key}"})
        image_bytes = base64.b64decode(image_b64_from_response(provider, payload))
    elif provider == "gemini":
        parts: list[dict[str, Any]] = [{"text": prompt}]
        inline = image_inline_payload(reference_path)
        if inline:
            parts.append({"inline_data": {"mime_type": inline["mimeType"], "data": inline["bytesBase64Encoded"]}})
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
        payload = post_json_request(url, {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"responseModalities": ["IMAGE"]}}, {})
        image_bytes = base64.b64decode(image_b64_from_response(provider, payload))
    else:
        raise ChainError(f"Unsupported image provider: {provider}/{model}")
    output_path.write_bytes(image_bytes)
    return {"provider": provider, "model": model, "output_path": str(output_path), "bytes": len(image_bytes)}


def operation_done(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or payload.get("task_status") or "").upper()
    return bool(payload.get("done")) or status in {"SUCCEEDED", "SUCCESS", "COMPLETED", "SUCCESSFUL", "DONE"}


def operation_failed(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or payload.get("task_status") or "").upper()
    if status in {"FAILED", "FAILURE", "CANCELED", "CANCELLED"}:
        return json.dumps(payload, ensure_ascii=False)[:1200]
    error = payload.get("error")
    return json.dumps(error, ensure_ascii=False)[:1200] if error else ""


def provider_video_seconds(provider: str, model: str, duration: float | None) -> int:
    value = int(round(float(duration or 4)))
    if provider == "xai":
        return max(3, min(value, 15))
    if provider == "openai":
        return 4 if value <= 4 else 8 if value <= 8 else 12
    if provider == "gemini":
        return 8
    if provider == "wan":
        return max(3, min(value, 15))
    return max(3, min(value, 15))


def generate_video(config: dict[str, str], prompt: str, output_path: Path, first_image: Path | None, duration: float | None = None) -> dict[str, Any]:
    provider = config["provider"]
    model = config["model"]
    api_key = config["api_key"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    seconds = provider_video_seconds(provider, model, duration)
    video_url = ""
    if provider == "xai":
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "duration": seconds, "aspect_ratio": "9:16", "resolution": "720p"}
        inline = image_inline_payload(first_image)
        if inline:
            payload["image"] = {"url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}"}
        started = post_json_request("https://api.x.ai/v1/videos/generations", payload, {"Authorization": f"Bearer {api_key}"})
        video_id = str(started.get("request_id") or started.get("id") or ((started.get("data") or {}).get("id") if isinstance(started.get("data"), dict) else "") or "")
        if not video_id:
            video_url = first_url(started)
        deadline = time.time() + 900
        while not video_url and time.time() < deadline:
            polled = get_json_request(f"https://api.x.ai/v1/videos/{urllib.parse.quote(video_id, safe='')}", {"Authorization": f"Bearer {api_key}"})
            failure = operation_failed(polled)
            if failure:
                raise ChainError(f"xAI video generation failed: {failure}")
            if str(polled.get("status") or "").lower() == "done" or operation_done(polled):
                video_url = first_url(polled)
                break
            time.sleep(5)
        if not video_url:
            raise ChainError("xAI video generation completed without a downloadable video URL")
        download_binary(video_url, output_path, {"Authorization": f"Bearer {api_key}"})
    else:
        raise ChainError(f"Unsupported video provider in real chain: {provider}/{model}")
    return {"provider": provider, "model": model, "duration": seconds, "output_path": str(output_path), "video_url": video_url}


def shot_text(shot: dict[str, Any]) -> str:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    for value in (reference.get("srt_text"), shot.get("spoken_script"), shot.get("subtitle_text")):
        if isinstance(value, str) and value.strip():
            return strip_srt_numbers(value)
    rebuild_direction = shot.get("rebuild_direction") if isinstance(shot.get("rebuild_direction"), dict) else {}
    return strip_srt_numbers(str(rebuild_direction.get("new_spoken_script") or rebuild_direction.get("direction") or ""))


def strip_srt_numbers(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        item = line.strip()
        if not item or item.isdigit() or "-->" in item:
            continue
        lines.append(item)
    return " ".join(lines).strip()


def first_reference_for_shot(workspace: Path, shot: dict[str, Any]) -> Path | None:
    marks = (shot.get("reference") or {}).get("scene_marks") if isinstance(shot.get("reference"), dict) else []
    if isinstance(marks, list):
        for mark in marks:
            frames = mark.get("keyframes") if isinstance(mark, dict) and isinstance(mark.get("keyframes"), dict) else {}
            value = str(frames.get("first") or frames.get("single") or "").strip()
            if value:
                candidate = workspace / value
                if candidate.exists():
                    return candidate
    frames = (shot.get("reference") or {}).get("keyframes") if isinstance(shot.get("reference"), dict) else []
    if isinstance(frames, list):
        for frame in frames:
            value = str(frame.get("path") or "").strip() if isinstance(frame, dict) else ""
            if value and (workspace / value).exists():
                return workspace / value
    return None


def build_image_prompt(shot: dict[str, Any]) -> str:
    text = shot_text(shot)
    return (
        "Create a realistic vertical 9:16 commercial short-video first frame. "
        "Use the provided keyframe only as visual reference for composition and subject continuity. "
        "Remove subtitles, watermarks, logos, UI text, and unreadable overlay text. "
        "Keep lighting, product context, character identity, and scene intent natural. "
        f"Shot narration/SRT intent: {text}"
    )


def build_video_prompt(shot: dict[str, Any]) -> str:
    text = shot_text(shot)
    return (
        "Generate a realistic vertical 9:16 short video from the first frame. "
        "Keep motion subtle and natural, preserve the subject and product continuity, avoid generated subtitles or visible text. "
        "Camera movement should be steady with small lifestyle motion matching the narration rhythm. "
        f"Narration intent: {text}"
    )


def ffprobe_duration(ffprobe: str, path: Path) -> float | None:
    try:
        result = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], check=True, capture_output=True, text=True, timeout=20)
        return round(float(result.stdout.strip()), 3)
    except Exception:
        return None


def find_ffmpeg() -> str:
    candidates = [
        Path("OpenCrew/.bin/ffmpeg").resolve(),
        Path("OpenCrew/vendor/static_ffmpeg/darwin_arm64/ffmpeg").resolve(),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "ffmpeg"


def find_ffprobe() -> str:
    candidates = [
        Path("OpenCrew/.bin/ffprobe").resolve(),
        Path("OpenCrew/vendor/static_ffmpeg/darwin_arm64/ffprobe").resolve(),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "ffprobe"


def run_command(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=600)
    if result.returncode != 0:
        raise ChainError(f"Command failed: {' '.join(args)}\nSTDOUT: {result.stdout[-2000:]}\nSTDERR: {result.stderr[-3000:]}")


def mux_shot_video(ffmpeg: str, ffprobe: str, video_path: Path, audio_path: Path, output_path: Path) -> dict[str, Any]:
    audio_duration = ffprobe_duration(ffprobe, audio_path) or 0
    video_duration = ffprobe_duration(ffprobe, video_path) or 0
    if audio_duration <= 0:
        raise ChainError(f"Unable to read audio duration: {audio_path}")
    if video_duration <= 0:
        raise ChainError(f"Unable to read video duration: {video_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        ffmpeg,
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-t",
        f"{audio_duration:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,format=yuv420p",
        "-r",
        "24",
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
        "-shortest",
        str(output_path),
    ])
    return {"output_path": str(output_path), "audio_duration": audio_duration, "source_video_duration": video_duration, "final_duration": ffprobe_duration(ffprobe, output_path)}


def concat_videos(ffmpeg: str, ffprobe: str, videos: list[Path], output_path: Path) -> dict[str, Any]:
    if not videos:
        raise ChainError("No shot videos to concat")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_path = output_path.with_suffix(".concat.txt")
    concat_path.write_text("\n".join(f"file '{str(path).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for path in videos) + "\n", encoding="utf-8")
    run_command([
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-c",
        "copy",
        str(output_path),
    ])
    return {"output_path": str(output_path), "duration": ffprobe_duration(ffprobe, output_path), "shot_count": len(videos)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real Plan A provider chain from DB configs.")
    default_workspace = opencrew_session_workspace(51)
    parser.add_argument("--workspace", type=Path, default=default_workspace)
    parser.add_argument("--task-id", type=int, default=1)
    parser.add_argument("--shot-id", action="append", default=[])
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--voice-id", default="Cherry")
    parser.add_argument("--skip-video", action="store_true")
    parser.add_argument("--skip-compose", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    try:
        database_url = resolve_database_url(args)
        context = fetch_rebuild_context(database_url, int(args.task_id))
        image_config = load_active_media_config(database_url, "image")
        tts_config = load_active_media_config(database_url, "tts")
        video_config = load_active_media_config(database_url, "video")
        plan = read_json(workspace / "rebuild_shot_plan.json")
        wanted = {str(item) for item in args.shot_id if str(item).strip()}
        shots = [shot for shot in (plan.get("shots") or []) if isinstance(shot, dict) and (not wanted or str(shot.get("shot_id") or "") in wanted)]
        if not shots:
            raise ChainError("No shots matched")
        results = []
        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()
        final_shot_videos: list[Path] = []
        for shot in shots:
            shot_id = str(shot.get("shot_id") or "")
            out_dir = workspace / "plan_a_real_chain" / shot_id
            text_value = shot_text(shot)
            reference = first_reference_for_shot(workspace, shot)
            if not text_value:
                raise ChainError(f"{shot_id} has no narration/SRT text")
            tts_path = out_dir / "locked.wav"
            image_path = out_dir / "first_frame.png"
            video_path = out_dir / "shot_video.mp4"
            final_video_path = out_dir / "plan_a.mp4"
            tts_result = run_logged_step(
                workspace,
                shot_id,
                "tts",
                {"config": tts_config, "text_length": len(text_value), "output_path": str(tts_path)},
                lambda: generate_tts_audio(tts_config, text_value, str(args.voice_id), "Natural energetic Chinese commercial narration.", tts_path),
            )
            image_result = run_logged_step(
                workspace,
                shot_id,
                "image",
                {"config": image_config, "reference": str(reference or ""), "output_path": str(image_path), "prompt": build_image_prompt(shot)[:1000]},
                lambda: generate_image(image_config, build_image_prompt(shot), image_path, reference),
            )
            video_result: dict[str, Any] = {"skipped": True}
            if not args.skip_video:
                video_result = run_logged_step(
                    workspace,
                    shot_id,
                    "video",
                    {"config": video_config, "first_image": str(image_path), "output_path": str(video_path), "prompt": build_video_prompt(shot)[:1000]},
                    lambda: generate_video(video_config, build_video_prompt(shot), video_path, image_path, safe_float(shot.get("duration"), 4.0)),
                )
                mux_result = run_logged_step(
                    workspace,
                    shot_id,
                    "mux",
                    {"video_path": str(video_path), "audio_path": str(tts_path), "output_path": str(final_video_path)},
                    lambda: mux_shot_video(ffmpeg, ffprobe, video_path, tts_path, final_video_path),
                )
                final_shot_videos.append(final_video_path)
            else:
                mux_result = {"skipped": True}
            results.append({
                "shot_id": shot_id,
                "tts": {**tts_result, "duration": ffprobe_duration(ffprobe, tts_path) if tts_path.exists() else None},
                "image": image_result,
                "video": video_result,
                "mux": mux_result,
            })
        assembly: dict[str, Any] = {"skipped": True}
        if not args.skip_video and not args.skip_compose:
            assembly = run_logged_step(
                workspace,
                "shot_plan",
                "assembly",
                {"shot_count": len(final_shot_videos), "output_path": str(workspace / "renders" / "plan_a_real_chain.mp4")},
                lambda: concat_videos(ffmpeg, ffprobe, final_shot_videos, workspace / "renders" / "plan_a_real_chain.mp4"),
            )
        report = {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "status": "completed",
            "task": context,
            "configs": {"image": redact_config(image_config), "tts": redact_config(tts_config), "video": redact_config(video_config)},
            "results": results,
            "assembly": assembly,
        }
        write_json(workspace / "reports" / "plan_a_real_chain_report.json", report)
    except Exception as exc:
        report = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "failed", "message": str(exc)}
        try:
            write_json(args.workspace.expanduser().resolve() / "reports" / "plan_a_real_chain_report.json", report)
        except Exception:
            pass
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
