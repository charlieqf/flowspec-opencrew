from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import random
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

TEMPLATE_NAME = "Ref_05_02_Lipsync_HeyGen.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Lipsync_HeyGen.md"
API_BASE_URL = "https://api.heygen.com/v3"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FFMPEG_PATH = REPO_ROOT / "ToolLibrary" / ".bin" / "ffmpeg"
DEFAULT_FFPROBE_PATH = REPO_ROOT / "ToolLibrary" / ".bin" / "ffprobe"


class ToolError(RuntimeError):
    pass


class ProviderTimeout(ToolError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def redact_secret_text(value: str) -> str:
    text = str(value or "")
    text = text.replace("\\/", "/")
    text = re.sub(r"([?&]key=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Authorization[\"']?\s*[:=]\s*[\"']?\s*Bearer\s+)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1***", text, flags=re.I)
    text = re.sub(r"(x-api-key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    return text


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def template_snapshot_text(context: dict[str, Any], default_name: str) -> str:
    prompt_dir = Path(context.get("prompt_dir") or "")
    template_name = text_value(context.get("template_name") or default_name)
    candidate = prompt_dir / template_name
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    source_value = text_value(context.get("template_source_path"))
    source = Path(source_value) if source_value else None
    if source and source.exists() and source.is_file():
        return source.read_text(encoding="utf-8")
    return ""


def _write_prompt_package_file(prompt_dir: Path, asset_key: str, kind: str, package: dict[str, Any]) -> Path:
    rendered_path = prompt_dir / f"PromptRendered_{asset_key}_{kind}Prompt.json"
    write_json(rendered_path, package)
    return rendered_path


def _template_text(context: dict[str, Any]) -> str:
    return template_snapshot_text({**context, "template_name": TEMPLATE_NAME, "template_source_path": str(SOURCE_TEMPLATE_PATH)}, TEMPLATE_NAME)


def _block(template_text: str, name: str) -> str:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    if start not in template_text or end not in template_text:
        raise ToolError(f"Lipsync HeyGen template is missing block marker: {name}")
    return template_text.split(start, 1)[1].split(end, 1)[0].strip()


def normalize_mode(model: str) -> str:
    value = text_value(model).lower() or "speed"
    aliases = {
        "heygen-lipsync-speed": "speed",
        "heygen-lipsync-precision": "precision",
    }
    value = aliases.get(value, value)
    if value not in {"speed", "precision"}:
        raise ToolError(f"Unsupported HeyGen lipsync model/mode: {model}. Expected speed or precision.")
    return value


def build_prompt_package(context: dict[str, Any]) -> dict[str, Any]:
    template_text = _template_text(context)
    segment = dict_value(context.get("segment"))
    prompt = f"{_block(template_text, 'LIPSYNC_HEYGEN_PROMPT')}\n\n{_block(template_text, 'LIPSYNC_HEYGEN_PITFALLS_APPEND_ONLY')}"
    return {
        "schema_version": "analysis_v1_05_02_lipsync_prompt_heygen_0.1",
        "prompt_type": "lipsync_request",
        "provider_profile": "lipsync_heygen",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "template_source": TEMPLATE_NAME,
        "template_snapshot_chars": len(template_text),
        "template_blocks": ["LIPSYNC_HEYGEN_PROMPT", "LIPSYNC_HEYGEN_PITFALLS_APPEND_ONLY"],
        "prompt": prompt,
        "extracted_fields": {
            "video_path": text_value(context.get("video_path")),
            "audio_path": text_value(context.get("audio_path")),
            "output_path": text_value(context.get("output_path")),
        },
    }


def write_prompt_package(prompt_dir: Path, asset_key: str, package: dict[str, Any]) -> Path:
    return _write_prompt_package_file(prompt_dir, asset_key, "LipSync", package)


def dry_run_prompt(context: dict[str, Any], prompt_dir: Path, asset_key: str) -> dict[str, Any]:
    package = build_prompt_package(context)
    return {"prompt_path": str(write_prompt_package(prompt_dir, asset_key, package)), "package": package}


def ffmpeg_binary() -> str:
    configured = text_value(os.environ.get("OPENCREW_FFMPEG_PATH"))
    if configured:
        return configured
    return str(DEFAULT_FFMPEG_PATH if DEFAULT_FFMPEG_PATH.exists() else "ffmpeg")


def ffprobe_binary() -> str:
    configured = text_value(os.environ.get("OPENCREW_FFPROBE_PATH"))
    if configured:
        return configured
    return str(DEFAULT_FFPROBE_PATH if DEFAULT_FFPROBE_PATH.exists() else "ffprobe")


def probe_media(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            ffprobe_binary(),
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=index,codec_type,codec_name,width,height,sample_rate,channels,color_space,color_transfer,color_primaries",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ToolError(f"Cannot probe HeyGen lipsync media {path.name}: {detail[:600]}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ToolError(f"Cannot parse ffprobe result for HeyGen lipsync media {path.name}.") from exc
    return payload if isinstance(payload, dict) else {}


def media_streams(path: Path) -> list[dict[str, Any]]:
    streams = probe_media(path).get("streams")
    return streams if isinstance(streams, list) else []


def video_stream(path: Path) -> dict[str, Any]:
    return next((dict_value(stream) for stream in media_streams(path) if dict_value(stream).get("codec_type") == "video"), {})


def restore_source_color_metadata(source_path: Path, output_path: Path) -> dict[str, Any]:
    try:
        source_stream = video_stream(source_path)
        output_stream = video_stream(output_path)
    except Exception as exc:
        return {"status": "failed", "source_color": {}, "error": redact_secret_text(str(exc))[:600]}
    source_color = {
        "color_space": text_value(source_stream.get("color_space")),
        "color_transfer": text_value(source_stream.get("color_transfer")),
        "color_primaries": text_value(source_stream.get("color_primaries")),
    }
    source_color = {key: value for key, value in source_color.items() if value and value != "unknown"}
    if not source_color or all(text_value(output_stream.get(key)) == value for key, value in source_color.items()):
        return {"status": "unchanged", "source_color": source_color}
    option_by_key = {"color_space": "-colorspace", "color_transfer": "-color_trc", "color_primaries": "-color_primaries"}
    color_args = [item for key, value in source_color.items() for item in (option_by_key[key], value)]
    remuxed_path = output_path.with_name(f"{output_path.stem}_color_metadata_{uuid.uuid4().hex[:8]}{output_path.suffix}")
    try:
        run_ffmpeg(
            [ffmpeg_binary(), "-y", "-i", str(output_path), "-map", "0", "-c", "copy", *color_args, "-movflags", "+faststart", str(remuxed_path)],
            "HeyGen output color metadata restoration",
        )
        os.replace(remuxed_path, output_path)
        restored_stream = video_stream(output_path)
        return {
            "status": "restored",
            "source_color": source_color,
            "output_color": {key: text_value(restored_stream.get(key)) for key in source_color},
        }
    except Exception as exc:
        remuxed_path.unlink(missing_ok=True)
        return {"status": "failed", "source_color": source_color, "error": redact_secret_text(str(exc))[:600]}


def media_has_audio(path: Path) -> bool:
    return any(dict_value(stream).get("codec_type") == "audio" for stream in media_streams(path))


def media_audio_codec(path: Path) -> str:
    for stream in media_streams(path):
        data = dict_value(stream)
        if data.get("codec_type") == "audio":
            return text_value(data.get("codec_name")).lower()
    return ""


def media_duration_seconds(path: Path) -> float:
    raw = dict_value(probe_media(path).get("format")).get("duration")
    try:
        duration = float(raw)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise ToolError(f"Cannot determine HeyGen lipsync media duration for {path.name}.")
    return duration


def run_ffmpeg(command: list[str], description: str) -> None:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{description} timed out while preparing HeyGen lipsync media.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ToolError(f"{description} failed: {detail[:1000]}")


def heygen_detection_snippet_duration(video_duration: float) -> float:
    return min(max(0.5, video_duration * 0.2), max(0.5, video_duration - 0.5))


def heygen_detection_audio_plan(video_duration: float, audio_duration: float) -> dict[str, float]:
    snippet_duration = min(heygen_detection_snippet_duration(video_duration), max(0.25, audio_duration))
    middle_start = video_duration * 0.25
    middle_end = video_duration * 0.75
    latest_insert_at = min(max(0.0, video_duration - snippet_duration), max(middle_start, middle_end - snippet_duration))
    if latest_insert_at > middle_start:
        insert_at = random.uniform(middle_start, latest_insert_at)
    else:
        insert_at = max(0.0, (video_duration - snippet_duration) / 2.0)
    latest_audio_start = max(0.0, audio_duration - snippet_duration)
    audio_start = random.uniform(0.0, latest_audio_start) if latest_audio_start > 0 else 0.0
    tempo = random.uniform(0.92, 1.12)
    return {
        "snippet_duration": snippet_duration,
        "insert_at": insert_at,
        "audio_start": audio_start,
        "audio_end": min(audio_duration, audio_start + snippet_duration),
        "tempo": tempo,
    }


def prepare_video_for_heygen(video_path: Path, working_dir: Path, driving_audio_path: Path | None = None) -> tuple[Path, dict[str, Any]]:
    source_streams = media_streams(video_path)
    has_audio = any(dict_value(stream).get("codec_type") == "audio" for stream in source_streams)
    if has_audio:
        return video_path, {"changed": False, "reason": "source_video_has_audio_stream"}
    if driving_audio_path is None or not driving_audio_path.exists():
        raise ToolError("HeyGen source video has no audio stream, and no driving audio is available to attach for speaker detection.")
    video_duration = media_duration_seconds(video_path)
    audio_duration = media_duration_seconds(driving_audio_path)
    detection_plan = heygen_detection_audio_plan(video_duration, audio_duration)
    snippet_duration = detection_plan["snippet_duration"]
    insert_at = detection_plan["insert_at"]
    insert_delay_ms = int(insert_at * 1000)
    prepared_path = working_dir / f"{video_path.stem}_heygen_video_with_detection_audio.mp4"
    run_ffmpeg(
        [
            ffmpeg_binary(),
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(driving_audio_path),
            "-filter_complex",
            (
                f"anullsrc=r=48000:cl=mono:d={video_duration:.6f}[sil];"
                f"[1:a]atrim={detection_plan['audio_start']:.6f}:{detection_plan['audio_end']:.6f},"
                f"asetpts=PTS-STARTPTS,atempo={detection_plan['tempo']:.3f},apad,"
                f"atrim=0:{snippet_duration:.6f},asetpts=PTS-STARTPTS,adelay={insert_delay_ms}:all=1[speech];"
                f"[sil][speech]amix=inputs=2:duration=first:dropout_transition=0,"
                f"atrim=0:{video_duration:.6f},asetpts=PTS-STARTPTS[a]"
            ),
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(prepared_path),
        ],
        "HeyGen source video audio-track normalization",
    )
    return prepared_path, {
        "changed": True,
        "reason": "source_video_missing_audio_stream_attached_sparse_detection_audio",
        "source_video_duration_seconds": video_duration,
        "driving_audio_duration_seconds": audio_duration,
        "detection_snippet_duration_seconds": snippet_duration,
        "detection_insert_at_seconds": insert_at,
        "detection_audio_source_start_seconds": detection_plan["audio_start"],
        "detection_audio_source_end_seconds": detection_plan["audio_end"],
        "detection_audio_tempo": detection_plan["tempo"],
        "detection_audio_policy": "single_random_middle_speech_snippet_20_percent_with_tempo_shift",
        "original_path": str(video_path),
        "attached_audio_path": str(driving_audio_path),
        "prepared_path": str(prepared_path),
    }


def prepare_audio_for_heygen(audio_path: Path, working_dir: Path) -> tuple[Path, dict[str, Any]]:
    codec = media_audio_codec(audio_path)
    is_real_wav = audio_path.suffix.lower() == ".wav" and codec in {"pcm_s16le", "pcm_s24le", "pcm_f32le", "pcm_f64le"}
    if is_real_wav:
        return audio_path, {"changed": False, "reason": "source_audio_is_pcm_wav", "codec": codec}
    prepared_path = working_dir / f"{audio_path.stem}_heygen_audio_pcm.wav"
    run_ffmpeg(
        [
            ffmpeg_binary(),
            "-y",
            "-i",
            str(audio_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(prepared_path),
        ],
        "HeyGen driving audio WAV normalization",
    )
    return prepared_path, {
        "changed": True,
        "reason": "source_audio_not_pcm_wav",
        "source_codec": codec,
        "original_path": str(audio_path),
        "prepared_path": str(prepared_path),
    }


def prepare_media_for_heygen(video_path: Path, audio_path: Path, working_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
    working_dir.mkdir(parents=True, exist_ok=True)
    prepared_audio, audio_info = prepare_audio_for_heygen(audio_path, working_dir)
    prepared_video, video_info = prepare_video_for_heygen(video_path, working_dir, prepared_audio)
    return prepared_video, prepared_audio, {"video": video_info, "audio": audio_info}


def retry_after_seconds(response: Any, attempt: int) -> float:
    raw = ""
    try:
        raw = text_value(response.headers.get("Retry-After"))
    except Exception:
        raw = ""
    try:
        seconds = float(raw)
        if seconds > 0:
            return max(1.0, min(seconds, 90.0))
    except (TypeError, ValueError):
        pass
    return min(15.0 * attempt, 60.0)


def _requests_proxy_tunnel_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "proxyerror" in message
        or "unable to connect to proxy" in message
        or "connection not allowed by ruleset" in message
        or "sockshttpsconnectionpool" in message
        or "sockshttpconnectionpool" in message
        or "tunnel connection failed" in message
        or ("proxy" in message and ("403" in message or "forbidden" in message or "connection refused" in message))
        or "127.0.0.1:7890" in message
        or "localhost:7890" in message
    )


def _request_with_direct_retry(requests_module: Any, method: str, url: str, *, on_retry: Any = None, **kwargs: Any) -> Any:
    try:
        return requests_module.request(method, url, **kwargs)
    except Exception as exc:
        if not _requests_proxy_tunnel_error(exc):
            raise
        if on_retry:
            on_retry()
        session = requests_module.Session()
        session.trust_env = False
        response = session.request(method, url, **kwargs)
        setattr(response, "_opencrew_direct_session", session)
        return response


def _close_response(response: Any) -> None:
    try:
        response.close()
    finally:
        session = getattr(response, "_opencrew_direct_session", None)
        if session:
            session.close()


def response_json(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_text": response.text}
    return payload if isinstance(payload, dict) else {"data": payload}


def data_object(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def lipsync_id_from_payload(payload: dict[str, Any]) -> str:
    data = data_object(payload)
    return text_value(data.get("lipsync_id") or data.get("id") or payload.get("lipsync_id") or payload.get("id"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def terminal_lipsync_failure(status_record: dict[str, Any]) -> bool:
    latest = dict_value(status_record.get("latest"))
    payload = dict_value(latest.get("body"))
    if not payload:
        history = list_value(status_record.get("history"))
        if history and isinstance(history[-1], dict):
            payload = dict_value(history[-1].get("body"))
    data = data_object(payload)
    status = text_value(data.get("status") or payload.get("status")).lower()
    return bool(failure_message_from_payload(payload)) or status in {"failed", "error", "rejected", "cancelled", "canceled"}


def resumable_lipsync_id(request_path: Path, status_path: Path, create_response_path: Path, video_path: Path, audio_path: Path, mode: str) -> str:
    request_record = read_json(request_path)
    if request_record:
        recorded_video = text_value(request_record.get("video_path"))
        recorded_audio = text_value(request_record.get("audio_path"))
        recorded_mode = text_value(request_record.get("mode"))
        if recorded_video and recorded_video != str(video_path):
            return ""
        if recorded_audio and recorded_audio != str(audio_path):
            return ""
        if recorded_mode and recorded_mode != mode:
            return ""
        for recorded_size, current_path in (
            (request_record.get("video_size_bytes"), video_path),
            (request_record.get("audio_size_bytes"), audio_path),
        ):
            if recorded_size in {None, ""}:
                continue
            try:
                size_matches = int(recorded_size) == current_path.stat().st_size
            except (OSError, TypeError, ValueError):
                size_matches = False
            if not size_matches:
                return ""
        for recorded_hash, current_path in (
            (text_value(request_record.get("video_sha256")), video_path),
            (text_value(request_record.get("audio_sha256")), audio_path),
        ):
            if recorded_hash and recorded_hash != file_sha256(current_path):
                return ""
    status_record = read_json(status_path)
    if terminal_lipsync_failure(status_record):
        return ""
    candidate = text_value(status_record.get("lipsync_id"))
    if candidate:
        return candidate
    create_record = read_json(create_response_path)
    body = create_record.get("body") if isinstance(create_record.get("body"), dict) else {}
    return lipsync_id_from_payload(body)


def upload_asset(requests: Any, api_key: str, path: Path, *, timeout: int) -> dict[str, Any]:
    url = f"{API_BASE_URL}/assets"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    idempotency_key = f"opencrew-{uuid.uuid4().hex}"
    with path.open("rb") as file_obj:
        response = _request_with_direct_retry(
            requests,
            "POST",
            url,
            headers={"x-api-key": api_key, "Idempotency-Key": idempotency_key},
            files={"file": (path.name, file_obj, mime)},
            timeout=timeout,
            on_retry=lambda: file_obj.seek(0),
        )
    try:
        payload = response_json(response)
        if int(response.status_code) >= 400:
            raise ToolError(f"HeyGen asset upload failed: HTTP {response.status_code}: {json_safe(payload)}")
        data = data_object(payload)
        asset_id = text_value(data.get("asset_id"))
        asset_url = text_value(data.get("url"))
        if not asset_id and not asset_url:
            raise ToolError(f"HeyGen asset upload response missing asset_id/url: {json_safe(payload)}")
        return {
            "asset_id": asset_id,
            "url": asset_url,
            "mime_type": text_value(data.get("mime_type")),
            "size_bytes": data.get("size_bytes"),
            "source_path": str(path),
        }
    finally:
        _close_response(response)


def asset_input(asset: dict[str, Any]) -> dict[str, str]:
    url = text_value(asset.get("url"))
    if url:
        return {"type": "url", "url": url}
    asset_id = text_value(asset.get("asset_id"))
    if asset_id:
        return {"type": "asset_id", "asset_id": asset_id}
    raise ToolError(f"HeyGen asset is missing url/asset_id: {json_safe(asset)}")


def optional_bool(config: dict[str, Any], key: str, default: bool | None = None) -> bool | None:
    if key not in config:
        return default
    value = config.get(key)
    if isinstance(value, bool):
        return value
    text = text_value(value).lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def create_payload(config: dict[str, Any], video_asset: dict[str, Any], audio_asset: dict[str, Any], mode: str, title: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "video": asset_input(video_asset),
        "audio": asset_input(audio_asset),
        "title": title,
        "mode": mode,
        "enable_caption": optional_bool(config, "enable_caption", False),
        "keep_the_same_format": optional_bool(config, "keep_the_same_format", True),
        "enable_dynamic_duration": optional_bool(config, "enable_dynamic_duration", True),
        "disable_music_track": optional_bool(config, "disable_music_track", False),
        "enable_speech_enhancement": optional_bool(config, "enable_speech_enhancement", False),
        "enable_watermark": optional_bool(config, "enable_watermark", False),
    }
    for key in ("callback_url", "callback_id", "fps_mode", "folder_id"):
        value = text_value(config.get(key))
        if value:
            payload[key] = value
    for key in ("start_time", "end_time"):
        if config.get(key) in (None, ""):
            continue
        try:
            payload[key] = float(config[key])
        except (TypeError, ValueError):
            raise ToolError(f"HeyGen {key} must be numeric when provided.")
    return {key: value for key, value in payload.items() if value is not None}


def output_url_from_payload(payload: dict[str, Any]) -> str:
    data = data_object(payload)
    return text_value(data.get("video_url") or data.get("url") or payload.get("video_url") or payload.get("url"))


def failure_message_from_payload(payload: dict[str, Any]) -> str:
    data = data_object(payload)
    return text_value(data.get("failure_message") or data.get("error") or payload.get("failure_message") or payload.get("error"))


def download_output(requests: Any, url: str, output_path: Path) -> int:
    response = _request_with_direct_retry(requests, "GET", url, stream=True, timeout=300)
    try:
        if int(response.status_code) in {401, 403}:
            _close_response(response)
            response = _request_with_direct_retry(requests, "GET", url, stream=True, timeout=300)
        response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        byte_count = 0
        with output_path.open("wb") as out:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    byte_count += len(chunk)
                    out.write(chunk)
        return byte_count
    finally:
        _close_response(response)


def poll_lipsync_until_output(requests: Any, api_key: str, lipsync_id: str, status_path: Path, output_path: Path, timeout_seconds: int) -> tuple[dict[str, Any], str, int]:
    status_record = read_json(status_path)
    existing_history = status_record.get("history")
    history: list[dict[str, Any]] = existing_history if isinstance(existing_history, list) else []
    final_payload: dict[str, Any] = {}
    latest = status_record.get("latest") if isinstance(status_record.get("latest"), dict) else {}
    latest_body = latest.get("body") if isinstance(latest.get("body"), dict) else {}
    if latest_body:
        final_payload = latest_body
    deadline = time.time() + timeout_seconds
    output_url = output_url_from_payload(final_payload)
    if output_url:
        data = data_object(final_payload)
        status = text_value(data.get("status") or final_payload.get("status")).lower()
        if status in {"", "completed", "complete", "done", "success", "succeeded"}:
            return final_payload, output_url, download_output(requests, output_url, output_path)
    while time.time() < deadline:
        poll_response = _request_with_direct_retry(requests, "GET", f"{API_BASE_URL}/lipsyncs/{lipsync_id}", headers={"x-api-key": api_key}, timeout=60)
        try:
            payload = response_json(poll_response)
            status_code = int(poll_response.status_code)
            data = data_object(payload)
            status = text_value(data.get("status") or payload.get("status")).lower()
            final_payload = payload
            history.append({"checked_at": now_ms(), "status_code": status_code, "status": status, "body": payload})
            write_json(status_path, {"lipsync_id": lipsync_id, "history": history, "latest": {"status_code": status_code, "body": payload}})
            if status_code >= 400:
                raise ToolError(f"HeyGen lipsync poll failed: HTTP {status_code}: {json_safe(payload)}")
            failure_message = failure_message_from_payload(payload)
            if failure_message or status in {"failed", "error", "rejected"}:
                raise ToolError(f"HeyGen lipsync failed: {failure_message or status}")
            output_url = output_url_from_payload(payload)
            if output_url and status in {"", "completed", "complete", "done", "success", "succeeded"}:
                break
        finally:
            _close_response(poll_response)
        time.sleep(15)
    if not output_url:
        output_url = output_url_from_payload(final_payload)
    if not output_url:
        raise ProviderTimeout("HeyGen lipsync timed out before returning video_url.")
    return final_payload, output_url, download_output(requests, output_url, output_path)


def generate(context: dict[str, Any], prompt_path: Path, output_path: Path) -> dict[str, Any]:
    del prompt_path
    try:
        import requests  # type: ignore
    except Exception as exc:
        raise ToolError("requests is required for HeyGen lipsync upload and polling.") from exc
    config = dict_value(context.get("config"))
    provider = text_value(config.get("provider")).lower()
    if provider not in {"heygen", "hey-gen"}:
        raise ToolError(f"Unsupported lipsync provider: {provider}/{config.get('model')}")
    api_key = text_value(config.get("api_key"))
    mode = normalize_mode(text_value(config.get("model") or "speed"))
    if not api_key:
        raise ToolError(f"Missing lipsync API key for {provider}/{mode}.")
    video_path = Path(text_value(context.get("video_path")))
    audio_path = Path(text_value(context.get("audio_path")))
    request_path = Path(text_value(context.get("request_path")))
    status_path = Path(text_value(context.get("status_path")))
    create_response_path = Path(text_value(context.get("create_response_path")))
    timeout_seconds = max(int(context.get("timeout_seconds") or 60), 60)
    title = text_value(config.get("title")) or f"OpenCrew lipsync {video_path.stem}"
    started_at = time.time()
    existing_lipsync_id = resumable_lipsync_id(request_path, status_path, create_response_path, video_path, audio_path, mode)
    if existing_lipsync_id:
        request_record = read_json(request_path)
        _final_payload, output_url, _bytes_written = poll_lipsync_until_output(requests, api_key, existing_lipsync_id, status_path, output_path, timeout_seconds)
        quality_preservation = restore_source_color_metadata(video_path, output_path)
        return {
            "provider": "heygen",
            "model": mode,
            "mode": mode,
            "lipsync_id": existing_lipsync_id,
            "output_url": output_url,
            "output_path": str(output_path),
            "upload_video_path": text_value(request_record.get("upload_video_path")) or str(video_path),
            "upload_audio_path": text_value(request_record.get("upload_audio_path")) or str(audio_path),
            "preparation": dict_value(request_record.get("preparation")),
            "bytes": output_path.stat().st_size,
            "video_asset": dict_value(request_record.get("video_asset")),
            "audio_asset": dict_value(request_record.get("audio_asset")),
            "quality_preservation": quality_preservation,
            "elapsed_seconds": round(time.time() - started_at, 3),
            "resumed": True,
        }

    for stale_path in (request_path, status_path, create_response_path):
        stale_path.unlink(missing_ok=True)

    upload_working_dir = request_path.parent / "HeyGenPreparedAssets"
    upload_video_path, upload_audio_path, preparation = prepare_media_for_heygen(video_path, audio_path, upload_working_dir)

    video_asset = upload_asset(requests, api_key, upload_video_path, timeout=120)
    audio_asset = upload_asset(requests, api_key, upload_audio_path, timeout=120)
    payload = create_payload(config, video_asset, audio_asset, mode, title)
    request_record = {
        "created_at": now_ms(),
        "endpoint": f"{API_BASE_URL}/lipsyncs",
        "provider": "heygen",
        "model": mode,
        "mode": mode,
        "video_path": str(video_path),
        "audio_path": str(audio_path),
        "upload_video_path": str(upload_video_path),
        "upload_audio_path": str(upload_audio_path),
        "preparation": preparation,
        "video_size_bytes": video_path.stat().st_size,
        "audio_size_bytes": audio_path.stat().st_size,
        "video_sha256": file_sha256(video_path),
        "audio_sha256": file_sha256(audio_path),
        "video_asset": video_asset,
        "audio_asset": audio_asset,
        "payload": payload,
    }
    write_json(request_path, request_record)

    create_attempts: list[dict[str, Any]] = []
    created: dict[str, Any] = {}
    response = None
    create_deadline = started_at + timeout_seconds
    for attempt in range(1, 5):
        response = _request_with_direct_retry(
            requests,
            "POST",
            f"{API_BASE_URL}/lipsyncs",
            headers={"x-api-key": api_key, "Content-Type": "application/json", "Idempotency-Key": f"opencrew-{uuid.uuid4().hex}"},
            json=payload,
            timeout=120,
        )
        try:
            created = response_json(response)
            status_code = int(response.status_code)
            retry_after = retry_after_seconds(response, attempt)
            create_attempts.append({
                "attempt": attempt,
                "checked_at": now_ms(),
                "status_code": status_code,
                "body": created,
                "retry_after_seconds": retry_after if status_code in {409, 429} else 0,
            })
            write_json(create_response_path, {"status_code": status_code, "body": created, "attempts": create_attempts})
            if status_code < 400:
                break
            if status_code in {409, 429} and attempt < 4 and time.time() + retry_after < create_deadline:
                time.sleep(retry_after)
                continue
            break
        finally:
            _close_response(response)
    if response is None:
        raise ToolError("HeyGen lipsync create failed before receiving a response")
    if int(response.status_code) >= 400:
        raise ToolError(f"HeyGen lipsync create failed: HTTP {response.status_code}: {json_safe(created)}")
    created_data = data_object(created)
    lipsync_id = text_value(created_data.get("lipsync_id") or created_data.get("id"))
    if not lipsync_id:
        raise ToolError(f"HeyGen lipsync create response did not include lipsync_id: {json_safe(created)}")

    _final_payload, output_url, _bytes_written = poll_lipsync_until_output(requests, api_key, lipsync_id, status_path, output_path, timeout_seconds)
    quality_preservation = restore_source_color_metadata(video_path, output_path)
    return {
        "provider": "heygen",
        "model": mode,
        "mode": mode,
        "lipsync_id": lipsync_id,
        "output_url": output_url,
        "output_path": str(output_path),
        "upload_video_path": str(upload_video_path),
        "upload_audio_path": str(upload_audio_path),
        "preparation": preparation,
        "bytes": output_path.stat().st_size,
        "video_asset": video_asset,
        "audio_asset": audio_asset,
        "quality_preservation": quality_preservation,
        "elapsed_seconds": round(time.time() - started_at, 3),
    }
