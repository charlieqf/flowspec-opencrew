from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import ssl
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

TOOL_ID = "07_02_Shot_PlanA_TTSGenerateAndLock"
TOOL_NAME = "07_02_Shot_PlanA_TTSGenerateAndLock"
TOOL_VERSION = "1.0.0"
TOOL_LEVEL = "shot"
REQUIRES = ['rebuild_shot_plan.json', 'tts_text']
PRODUCES = ["reports/plan_a/07_02_Shot_PlanA_TTSGenerateAndLock.json"]
SUGGESTED_PREVIOUS_TOOLS = ['07_01_Shot_PlanA_TTSPromptBuild']
SUGGESTED_NEXT_TOOLS = ['07_03_Shot_PlanA_TTSTimelineValidate']
DEFAULT_TTS_PROVIDER = "qwen"
DEFAULT_TTS_MODEL = "qwen3-tts-instruct-flash"
DEFAULT_TTS_VOICE = "Cherry"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
CONFIG_TABLE = "tool_media_provider_configs"
TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
from opencrew_runtime_secrets import apply_provider_proxy, resolve_secret_value

class ToolError(RuntimeError):
    pass

def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))

def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

def now_ms() -> int:
    return int(time.time() * 1000)

def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "")) or "item"

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
        raise ToolError("PostgreSQL driver psycopg is not available") from exc
    return psycopg.connect(normalize_database_url(database_url))

def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")

def load_active_media_config(database_url: str, kind: str) -> dict[str, str]:
    env_provider = os.environ.get(f"OPENCREW_{kind.upper()}_PROVIDER", "").strip()
    env_model = os.environ.get(f"OPENCREW_{kind.upper()}_MODEL", "").strip()
    env_key = os.environ.get(f"OPENCREW_{kind.upper()}_API_KEY", "").strip()
    if env_provider and env_model and env_key:
        return {"kind": kind, "provider": env_provider, "model": env_model, "api_key": env_key}
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
SELECT provider, model, api_key_ref, api_key_ciphertext
FROM {CONFIG_TABLE}
WHERE kind = %s AND active = TRUE AND enabled = TRUE
LIMIT 1
""", (kind,))
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

def redact_config(config: dict[str, str]) -> dict[str, str | int]:
    return {key: (len(str(value)) if key == "api_key" else value) for key, value in config.items()}

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

def find_ffprobe() -> str:
    for candidate in (Path("OpenCrew/.bin/ffprobe").resolve(), Path("OpenCrew/vendor/static_ffmpeg/darwin_arm64/ffprobe").resolve()):
        if candidate.exists():
            return str(candidate)
    return "ffprobe"

def ffprobe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run([find_ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], check=True, capture_output=True, text=True, timeout=20)
        return round(float(result.stdout.strip()), 3)
    except Exception:
        return None

def rel(workspace: Path, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(workspace.resolve()))
    except Exception:
        return str(path)

def resolve_workspace_path(workspace: Path, path_value: str) -> Path:
    path = Path(str(path_value or "")).expanduser()
    return path if path.is_absolute() else workspace / path

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
    return shots

def target_scene_marks(shot: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    scene_mark_id = str(getattr(args, "scene_mark_id", "") or "")
    marks = scene_marks_for_shot(shot)
    if scene_mark_id:
        marks = [mark for mark in marks if scene_id_of(mark) == scene_mark_id]
        if not marks:
            raise ToolError(f"Scene mark not found: {scene_mark_id}")
    return marks

def scene_frame_paths(mark: dict[str, Any]) -> tuple[str, str]:
    keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
    single = str(keyframes.get("single") or "").strip()
    first = str(keyframes.get("first") or single).strip()
    last = str(keyframes.get("last") or single or first).strip()
    return first, last

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

def variant_scene_dir(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return workspace / "Assets" / variant_id / safe_name(shot_id) / safe_name(scene_mark_id)

def canonical_scene_image_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "first.png"

def scene_asset_manifest_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "asset_manifest.json"

def variant_shot_dir(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return workspace / "Assets" / variant_id / safe_name(shot_id)

def shot_tts_prompt_report_text(workspace: Path, shot_id: str) -> str:
    path = variant_shot_dir(workspace, shot_id) / "reports" / "plan_a_07_01_tts_prompt_build.json"
    if not path.exists():
        return ""
    try:
        report = read_json(path)
    except Exception:
        return ""
    items = report.get("items") if isinstance(report, dict) else []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and str(item.get("shot_id") or "") == shot_id:
            return strip_srt_timing(str(item.get("text") or ""))
    return ""

def shot_tts_dir(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / "tts"

def canonical_locked_tts_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "locked.wav"

def canonical_shot_srt_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "shot.srt"

def canonical_shot_tts_timeline_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "shot_tts_timeline.locked.json"

def plan_a_shot_video_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / "plan_a.mp4"

def source_workspace_from_package(workspace: Path, source_package: dict[str, Any]) -> Path | None:
    for key in ("analysis_workspace", "workspace", "source_workspace"):
        value = str(source_package.get(key) or "").strip()
        if value:
            path = Path(value).expanduser()
            return path if path.is_absolute() else workspace / path
    return None

def resolve_existing_path(workspace: Path, source_package: dict[str, Any], path_value: str) -> Path | None:
    if not path_value:
        return None
    candidates = [resolve_workspace_path(workspace, path_value)]
    source_workspace = source_workspace_from_package(workspace, source_package)
    if source_workspace:
        candidates.append(resolve_workspace_path(source_workspace, path_value))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None

def srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def parse_srt_entries(text: str) -> list[dict[str, Any]]:
    entries = []
    for block in str(text or "").replace("\r\n", "\n").split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing = next((line for line in lines if "-->" in line), "")
        if not timing:
            continue
        body = " ".join(line for line in lines if line != timing and not line.isdigit()).strip()
        entries.append({"timing": timing, "text": body})
    return entries

def ensure_shot_srt_text(shot: dict[str, Any], duration: float, text_override: str = "") -> str:
    if text_override.strip():
        return f"1\n00:00:00,000 --> {srt_timestamp(max(0.1, duration))}\n{strip_srt_timing(text_override)}\n"
    text = str(shot.get("srt_text") or shot.get("shot_srt") or "").strip()
    if text and "-->" in text:
        return text
    clean = strip_srt_timing(text or shot_tts_source(shot).get("text") or spoken_script(shot))
    return f"1\n00:00:00,000 --> {srt_timestamp(max(0.1, duration))}\n{clean}\n"

def scene_image_items(workspace: Path, shot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    shot_id = shot_id_of(shot)
    for mark in scene_marks_for_shot(shot):
        scene_id = scene_id_of(mark)
        image = canonical_scene_image_path(workspace, shot_id, scene_id)
        if image.exists():
            rows.append({"shot_id": shot_id, "scene_mark_id": scene_id, "image": rel(workspace, image), "text": scene_text(mark, spoken_script(shot))})
    return rows

def build_shot_tts_timeline(workspace: Path, shot: dict[str, Any], audio_duration: float, srt_text: str) -> dict[str, Any]:
    shot_id = shot_id_of(shot)
    images = scene_image_items(workspace, shot)
    duration = max(0.1, safe_float(audio_duration, safe_float(shot.get("duration"), 1.0)))
    page_duration = duration / max(1, len(images))
    pages = []
    for index, item in enumerate(images):
        start = round(index * page_duration, 3)
        end = round(duration if index == len(images) - 1 else (index + 1) * page_duration, 3)
        pages.append({**item, "start": start, "end": end, "duration": round(max(0.1, end - start), 3)})
    return {"shot_id": shot_id, "duration": duration, "audio": rel(workspace, canonical_locked_tts_path(workspace, shot_id)), "shot_srt": rel(workspace, canonical_shot_srt_path(workspace, shot_id)), "srt_entries": parse_srt_entries(srt_text), "image_pages": pages, "generated_at": now_ms()}

def scope_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {"shot_id": getattr(args, "shot_id", []), "scene_mark_id": getattr(args, "scene_mark_id", "")}

def enforce_scope(args: argparse.Namespace) -> list[dict[str, Any]]:
    missing = []
    shot_count = len(getattr(args, "shot_id", []) or [])
    scene_id = str(getattr(args, "scene_mark_id", "") or "")
    if TOOL_LEVEL in {"shot", "scene"} and shot_count != 1:
        missing.append({"dependency": "shot_id", "reason": f"{TOOL_LEVEL}-level tool requires exactly one --shot-id", "suggested_tools": [], "scope": scope_payload(args)})
    if TOOL_LEVEL == "scene" and not scene_id:
        missing.append({"dependency": "scene_mark_id", "reason": "scene-level tool requires --scene-mark-id", "suggested_tools": [], "scope": scope_payload(args)})
    return missing

def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    missing = enforce_scope(args)
    satisfied = []
    warnings = []
    plan_path = workspace / args.input
    plan: dict[str, Any] = {}
    if not plan_path.exists():
        missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"required workspace file does not exist: {args.input}", "suggested_tools": ["02_Rebuild_ShotPlanBuilder"], "scope": scope_payload(args)})
    else:
        satisfied.append("rebuild_shot_plan.json")
        try:
            plan = read_json(plan_path)
        except Exception as exc:
            missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"failed to read shot plan: {exc}", "suggested_tools": ["02_Rebuild_ShotPlanBuilder"], "scope": scope_payload(args)})
    if plan:
        try:
            shots = target_shots(plan, args)
        except Exception as exc:
            missing.append({"dependency": "target_scope", "reason": str(exc), "suggested_tools": [], "scope": scope_payload(args)})
            shots = []
        if "source_package.json" in REQUIRES:
            if (workspace / args.source_package).exists() or (workspace / "rebuild" / args.source_package).exists():
                satisfied.append("source_package.json")
            else:
                warnings.append({"dependency": "source_package.json", "reason": "source package not found; only workspace-local paths can be resolved", "scope": scope_payload(args)})
        if "confirmed_first_last" in REQUIRES:
            unconfirmed = []
            for shot in shots:
                for mark in target_scene_marks(shot, args):
                    status = mark.get("mark_status") if isinstance(mark.get("mark_status"), dict) else {}
                    if not status.get("first_last_confirmed"):
                        unconfirmed.append(f"{shot_id_of(shot)}/{scene_id_of(mark)}")
            if unconfirmed:
                missing.append({"dependency": "confirmed_first_last", "reason": f"unconfirmed scene marks: {', '.join(unconfirmed[:10])}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
            else:
                satisfied.append("confirmed_first_last")
        if "scene_first_images" in REQUIRES or "scene_assets" in REQUIRES:
            absent = []
            for shot in shots:
                for mark in target_scene_marks(shot, args):
                    if not canonical_scene_image_path(workspace, shot_id_of(shot), scene_id_of(mark)).exists():
                        absent.append(f"{shot_id_of(shot)}/{scene_id_of(mark)}")
            dep = "scene_first_images" if "scene_first_images" in REQUIRES else "scene_assets"
            if absent:
                missing.append({"dependency": dep, "reason": f"missing scene first images: {', '.join(absent[:10])}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
            else:
                satisfied.append(dep)
        if "scene_srt" in REQUIRES or "tts_text" in REQUIRES:
            absent = [shot_id_of(shot) for shot in shots if not shot_tts_source(shot).get("text")]
            dep = "scene_srt" if "scene_srt" in REQUIRES else "tts_text"
            if absent:
                missing.append({"dependency": dep, "reason": f"missing TTS text for shots: {', '.join(absent[:10])}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
            else:
                satisfied.append(dep)
        if "tts_selection" in REQUIRES:
            satisfied.append("tts_selection")
        for dep, fn in (("locked_tts", canonical_locked_tts_path), ("shot_srt", canonical_shot_srt_path), ("shot_tts_timeline", canonical_shot_tts_timeline_path)):
            if dep in REQUIRES:
                absent = [shot_id_of(shot) for shot in shots if not fn(workspace, shot_id_of(shot)).exists()]
                if absent:
                    missing.append({"dependency": dep, "reason": f"missing {dep} for shots: {', '.join(absent[:10])}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
                else:
                    satisfied.append(dep)
        if "plan_a_videos" in REQUIRES:
            absent = [shot_id_of(shot) for shot in shots if not plan_a_shot_video_path(workspace, shot_id_of(shot)).exists()]
            if absent:
                missing.append({"dependency": "plan_a_videos", "reason": f"missing shot videos: {', '.join(absent[:10])}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
            else:
                satisfied.append("plan_a_videos")
    return {"status": "blocked" if missing else "warning" if warnings else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": warnings}

def tts_generate_and_lock(workspace: Path, plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    items, blockers = [], []
    database_url = resolve_database_url(args)
    for shot in target_shots(plan, args):
        shot_id = shot_id_of(shot); text_value = (str(shot_tts_source(shot).get("text") or "") or shot_tts_prompt_report_text(workspace, shot_id)).strip(); locked_path = canonical_locked_tts_path(workspace, shot_id); srt_path = canonical_shot_srt_path(workspace, shot_id); timeline_path = canonical_shot_tts_timeline_path(workspace, shot_id); row = {"shot_id": shot_id, "locked_tts_path": rel(workspace, locked_path), "shot_srt_path": rel(workspace, srt_path), "timeline_path": rel(workspace, timeline_path)}
        if not text_value: blockers.append(f"{shot_id}: missing_tts_text"); items.append({**row, "status": "blocked", "blocking_reason": "missing_tts_text"}); continue
        plan_a = shot.get("plan_a") if isinstance(shot.get("plan_a"), dict) else {}
        final_package = shot.get("final_prompt_package") if isinstance(shot.get("final_prompt_package"), dict) else {}
        final_tts = final_package.get("tts") if isinstance(final_package.get("tts"), dict) else {}
        prompt = str(final_tts.get("execution_prompt") or plan_a.get("tts_prompt") or "Natural energetic Chinese commercial narration.")
        selection = shot.get("tts_selection") if isinstance(shot.get("tts_selection"), dict) else {}
        if not final_tts.get("execution_prompt"):
            prompt_template = str(selection.get("prompt_template") or "").strip()
            if prompt_template:
                prompt = prompt_template.replace("{text}", text_value)
            elif str(selection.get("prompt") or "").strip():
                prompt = str(selection.get("prompt") or "").strip()
        selection_provider = str(selection.get("provider") or "").strip()
        selection_model = str(selection.get("model") or "").strip()
        tts_config = load_media_config(database_url, "tts", selection_provider, selection_model)
        config_provider = str(tts_config.get("provider") or "").strip()
        selection_matches_config = bool(selection_provider and selection_provider == config_provider)
        voice_id = str((selection.get("voice") if selection_matches_config else "") or args.tts_voice or DEFAULT_TTS_VOICE)
        if args.force or args.force_tts_refresh or not locked_path.exists() or locked_path.stat().st_size <= 0:
            tts_result = generate_tts_audio(tts_config, text_value, voice_id, prompt, locked_path)
            source = "generated_tts"
        else:
            tts_result = {"provider": tts_config.get("provider", ""), "model": tts_config.get("model", ""), "voice_id": voice_id, "output_path": str(locked_path)}
            source = "existing_locked_tts"
        duration = ffprobe_duration(locked_path)
        if not duration or duration <= 0:
            blockers.append(f"{shot_id}: locked_tts_duration_unreadable"); items.append({**row, "status": "blocked", "blocking_reason": "locked_tts_duration_unreadable", "source": source}); continue
        srt_text = ensure_shot_srt_text(shot, duration, text_value); write_text(srt_path, srt_text); write_json(timeline_path, build_shot_tts_timeline(workspace, shot, duration, srt_text)); write_json(shot_tts_dir(workspace, shot_id) / "tts_manifest.json", {**row, "status": "locked", "text": text_value, "tts_prompt": prompt, "provider": tts_result.get("provider"), "model": tts_result.get("model"), "voice": voice_id, "duration": duration, "generated_at": now_ms(), "source": source, "config": redact_config(tts_config)}); items.append({**row, "status": "locked", "source": source, "provider": tts_result.get("provider"), "model": tts_result.get("model"), "voice": voice_id, "duration": duration})
    return {"status": "completed_with_blockers" if blockers else "completed", "tool_id": TOOL_ID, "blocking_errors": blockers, "results": items}

def run_tool(workspace: Path, plan: dict[str, Any], source_package: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    return tts_generate_and_lock(workspace, plan, args)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--shot-id", action="append", default=[])
    parser.add_argument("--scene-mark-id", default="")
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--tts-provider", default=DEFAULT_TTS_PROVIDER)
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL)
    parser.add_argument("--tts-voice", default=DEFAULT_TTS_VOICE)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default="OPENCREW_DATABASE_URL")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-tts-refresh", action="store_true")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    dependencies = {"status": "unknown", "satisfied": [], "missing": [], "warnings": []}
    try:
        dependencies = check_dependencies(workspace, args)
        if args.check_dependencies_only:
            result = None
            status = "blocked" if dependencies["missing"] else "completed_with_warnings" if dependencies["warnings"] else "completed"
        elif dependencies["missing"] and not args.force:
            result = None
            status = "blocked"
        else:
            plan = load_plan(workspace, args.input)
            source_package = load_source_package(workspace, args.source_package)
            result = run_tool(workspace, plan, source_package, args)
            status = str(result.get("status") or "completed")
            write_json(workspace / "reports" / "plan_a" / f"{TOOL_ID}.json", result)
            if args.output:
                write_json(workspace / args.output, plan)
        payload = {"tool": TOOL_ID, "tool_version": TOOL_VERSION, "status": status, "workspace": str(workspace), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS, "result": result}
    except Exception as exc:
        payload = {"tool": TOOL_ID, "tool_version": TOOL_VERSION, "status": "failed", "workspace": str(workspace), "message": str(exc), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS}
    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] == "blocked":
        raise SystemExit(2)
    if payload["status"] == "failed":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
