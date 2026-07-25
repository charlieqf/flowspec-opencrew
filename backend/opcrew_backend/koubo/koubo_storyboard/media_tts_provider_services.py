from __future__ import annotations

import asyncio
import base64
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
import wave
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from opcrew_backend.adapters.opencode import OpenCodeSessionClient
from opcrew_backend.context import now_ms
from opcrew_backend.routes.media_model_config import (
    CONFIG_TABLE,
    bytedance_tts_audio_bytes,
    dashscope_cosyvoice_tts_audio_bytes,
    ensure_table,
    load_stored_key,
)
from opcrew_backend.services.media_sanitize import MediaSanitizeError, sanitize_audio_file_metadata

from .constants import *
from .io_utils import read_json, safe_workspace_rel, write_json
from .runtime import analysis_tool_env
from .text_utils import redact_payload, redact_secret_text


SERVICE_EXPORTS = (
    "normalize_tts_model",
    "wav_data_from_pcm",
    "media_binary_candidates",
    "media_binary",
    "audio_duration_seconds",
    "ffmpeg_binary",
    "atempo_filter_chain",
    "tempo_stretch_audio",
    "load_tts_config",
    "dashscope_language_type",
    "first_audio_url",
    "first_audio_data",
    "generate_tts_audio",
)


TTS_CONTROL_TAG_RE = re.compile(r"[\[\(（【]\s*([A-Za-z][A-Za-z0-9 _-]{0,48}|[\u4e00-\u9fffA-Za-z0-9 _-]{1,48})\s*[\]\)）】]")

TTS_EMOTION_ALIASES: dict[str, tuple[str, int]] = {
    "happy": ("happy", 4),
    "cheerful": ("happy", 4),
    "joyful": ("happy", 4),
    "开心": ("happy", 4),
    "快乐": ("happy", 4),
    "高兴": ("happy", 4),
    "sad": ("sad", 4),
    "sadness": ("sad", 4),
    "悲伤": ("sad", 4),
    "难过": ("sad", 4),
    "伤心": ("sad", 4),
    "angry": ("angry", 5),
    "anger": ("angry", 5),
    "生气": ("angry", 5),
    "愤怒": ("angry", 5),
    "surprised": ("surprised", 4),
    "surprise": ("surprised", 4),
    "惊讶": ("surprised", 4),
    "惊喜": ("surprised", 4),
    "fear": ("fear", 4),
    "scared": ("fear", 4),
    "afraid": ("fear", 4),
    "恐惧": ("fear", 4),
    "害怕": ("fear", 4),
    "hate": ("hate", 4),
    "disgusted": ("hate", 4),
    "disgust": ("hate", 4),
    "厌恶": ("hate", 4),
    "嫌弃": ("hate", 4),
    "excited": ("excited", 5),
    "exciting": ("excited", 5),
    "激动": ("excited", 5),
    "兴奋": ("excited", 5),
    "shouting": ("excited", 5),
    "shout": ("excited", 5),
    "loud": ("excited", 5),
    "大声": ("excited", 5),
    "大喊": ("excited", 5),
    "喊叫": ("excited", 5),
}

TTS_SPEED_ALIASES: tuple[tuple[tuple[str, ...], float], ...] = (
    (("very fast", "super fast", "extremely fast", "非常快", "很快", "特快", "语速很快"), 1.35),
    (("fast", "quick", "rapid", "快速", "快节奏", "语速快"), 1.2),
    (("very slow", "super slow", "extremely slow", "非常慢", "很慢", "特慢", "语速很慢"), 0.7),
    (("slow", "slower", "慢速", "放慢", "语速慢"), 0.85),
    (("normal speed", "medium speed", "中速", "正常语速"), 1.0),
)


def normalize_tts_control_token(value: str) -> str:
    compact = re.sub(r"[\s_-]+", " ", str(value or "").strip().lower())
    return compact


def strip_embedded_tts_text(value: str) -> str:
    return re.sub(
        r"(?:朗读文本|正文|Text|只朗读当前句[^\n:：]{0,32})\s*[:：]\s*[\s\S]*$",
        "",
        str(value or ""),
        flags=re.IGNORECASE,
    ).strip()


def tts_control_from_token(value: str) -> dict[str, Any]:
    raw = str(value or "").strip()
    normalized = normalize_tts_control_token(raw)
    compact = re.sub(r"\s+", "", raw.lower())
    controls: dict[str, Any] = {}
    for aliases, speed_ratio in TTS_SPEED_ALIASES:
        if normalized in aliases or compact in aliases:
            controls["speed_ratio"] = speed_ratio
            break
    emotion = TTS_EMOTION_ALIASES.get(normalized) or TTS_EMOTION_ALIASES.get(compact)
    if emotion:
        controls["emotion"], controls["emotion_scale"] = emotion
    return controls


def merge_tts_controls(target: dict[str, Any], source: dict[str, Any]) -> None:
    if source.get("speed_ratio") is not None:
        target["speed_ratio"] = source["speed_ratio"]
    if source.get("emotion"):
        target["emotion"] = source["emotion"]
        target["emotion_scale"] = source.get("emotion_scale") or 4


def strip_tts_control_tags(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return "" if tts_control_from_token(match.group(1)) else match.group(0)

    stripped = TTS_CONTROL_TAG_RE.sub(replace, str(value or ""))
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)
    stripped = re.sub(r"\s+([，。！？、；：,.!?;:])", r"\1", stripped)
    return stripped.strip()


def tts_prompt_control_values(prompt: str, text_value: str) -> dict[str, Any]:
    controls: dict[str, Any] = {"text": strip_tts_control_tags(text_value)}
    source_values = [str(text_value or ""), str(prompt or "")]
    for source in source_values:
        for match in TTS_CONTROL_TAG_RE.finditer(source):
            merge_tts_controls(controls, tts_control_from_token(match.group(1)))

    instruction = strip_embedded_tts_text(prompt)
    normalized_instruction = normalize_tts_control_token(instruction)
    compact_instruction = re.sub(r"[\s_-]+", "", instruction.lower())
    for aliases, speed_ratio in TTS_SPEED_ALIASES:
        if any(alias in normalized_instruction or alias in compact_instruction for alias in aliases):
            controls["speed_ratio"] = speed_ratio
            break
    for alias, (emotion, scale) in TTS_EMOTION_ALIASES.items():
        if alias in normalized_instruction or alias in compact_instruction:
            controls["emotion"] = emotion
            controls["emotion_scale"] = scale
            break
    return controls


def normalize_tts_model(provider: str, model: str) -> str:
    provider_id = str(provider or "").strip()
    model_id = str(model or "").strip()
    if provider_id == "google" and model_id == "gemini-3.1-flash-tts":
        return "gemini-3.1-flash-tts-preview"
    return model_id


def wav_data_from_pcm(pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    import io
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    wav = io.BytesIO()
    wav.write(b"RIFF")
    wav.write((36 + len(pcm_data)).to_bytes(4, "little"))
    wav.write(b"WAVEfmt ")
    wav.write((16).to_bytes(4, "little"))
    wav.write((1).to_bytes(2, "little"))
    wav.write((channels).to_bytes(2, "little"))
    wav.write((sample_rate).to_bytes(4, "little"))
    wav.write((byte_rate).to_bytes(4, "little"))
    wav.write((block_align).to_bytes(2, "little"))
    wav.write((bits_per_sample).to_bytes(2, "little"))
    wav.write(b"data")
    wav.write((len(pcm_data)).to_bytes(4, "little"))
    wav.write(pcm_data)
    return wav.getvalue()


def write_sanitized_audio_bytes(output_path: Path, content: bytes) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)
    try:
        sanitize_audio_file_metadata(output_path)
    except MediaSanitizeError as exc:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=f"TTS audio metadata sanitization failed: {exc}") from exc


def media_binary_candidates(name: str) -> list[Path]:
    root = Path(__file__).resolve().parents[4]
    return [
        root / ".bin" / name,
        root / "ToolLibrary" / ".bin" / name,
        root / "ToolLibrary" / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
        root / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
    ]


def media_binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for candidate in media_binary_candidates(name):
        if candidate.exists():
            return str(candidate)
    return ""


def audio_duration_seconds(path: Path) -> float:
    ffprobe = media_binary("ffprobe")
    if ffprobe and Path(ffprobe).exists():
        try:
            result = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, timeout=20, check=False)
            value = float((result.stdout or "").strip())
            if value > 0:
                return round(value, 3)
        except Exception:
            pass
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            return round(frames / rate, 3) if rate else 0.0
    except Exception:
        return 0.0


def ffmpeg_binary() -> str:
    found = media_binary("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ffmpeg is unavailable: {exc}") from exc


def atempo_filter_chain(tempo: float) -> str:
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


def tempo_stretch_audio(source: Path, target: Path, tempo: float | None) -> dict[str, Any]:
    raw_duration = audio_duration_seconds(source)
    tempo_value = float(tempo or 1.0)
    if tempo_value <= 0 or raw_duration <= 0:
        tempo_value = 1.0
    if abs(tempo_value - 1.0) < 0.0001:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return {"raw_duration": raw_duration, "speed_factor": 1.0, "tempo": 1.0, "stretched": False, "warnings": []}
    target.parent.mkdir(parents=True, exist_ok=True)
    filters = ["aresample=48000", "aformat=channel_layouts=stereo", atempo_filter_chain(tempo_value), "loudnorm=I=-17:LRA=11:TP=-1.5", "asetpts=N/SR/TB"]
    cmd = [ffmpeg_binary(), "-y", "-i", str(source), "-af", ",".join(filters), "-ar", "48000", "-ac", "2", "-vn", str(target)]
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    if completed.returncode != 0:
        raise HTTPException(status_code=500, detail=(completed.stderr or "ffmpeg tempo stretch failed")[:2000])
    return {"raw_duration": raw_duration, "speed_factor": round(tempo_value, 4), "tempo": round(tempo_value, 4), "stretched": True, "warnings": []}


def load_tts_config(provider: str, model: str, *, sc: Any) -> dict[str, Any]:
    requested_provider = str(provider or "").strip()
    config_kind = "voice-clone" if requested_provider in {"cosyvoice", "heygen", "minimax"} else "tts"
    ensure_table(sc.ctx)
    with sc.ctx.engine.begin() as conn:
        row = conn.execute(sql_text(f"""
SELECT provider, model, api_key_ref, api_key_ciphertext, extra_json FROM {CONFIG_TABLE}
WHERE kind = :kind AND provider = :provider AND enabled = TRUE
LIMIT 1
"""), {"kind": config_kind, "provider": requested_provider}).first()
    if not row and requested_provider in {"cosyvoice", "minimax"}:
        config_kind = "tts"
        with sc.ctx.engine.begin() as conn:
            row = conn.execute(sql_text(f"""
SELECT provider, model, api_key_ref, api_key_ciphertext, extra_json FROM {CONFIG_TABLE}
WHERE kind = :kind AND provider = :provider AND enabled = TRUE
LIMIT 1
"""), {"kind": config_kind, "provider": requested_provider}).first()
    if not row:
        raise HTTPException(status_code=400, detail=f"TTS provider is not configured or enabled: {provider}")
    mapping = row._mapping
    api_key = str(load_stored_key(sc.ctx, config_kind, requested_provider) or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail=f"TTS provider API key is missing: {provider}")
    extra_json: dict[str, Any] = {}
    try:
        loaded_extra = json.loads(str(mapping.get("extra_json") or "{}"))
        extra_json = loaded_extra if isinstance(loaded_extra, dict) else {}
    except json.JSONDecodeError:
        extra_json = {}
    stored_provider = str(mapping.get("provider") or provider)
    stored_model = str(mapping.get("model") or "").strip()
    model_id = normalize_tts_model(stored_provider, model.strip() or stored_model)
    return {"provider": stored_provider, "model": model_id, "api_key": api_key, "extra": extra_json, **extra_json}


def dashscope_language_type(language: str) -> str:
    mapping = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean", "de": "German", "fr": "French", "ru": "Russian", "pt": "Portuguese", "es": "Spanish", "it": "Italian"}
    return mapping.get(str(language or "").strip().lower(), language or "Chinese")


def first_audio_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("url", "audio_url", "download_url", "uri"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for value in payload.values():
            found = first_audio_url(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = first_audio_url(value)
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


def generate_tts_audio(config: dict[str, Any], text_value: str, voice_id: str, prompt: str, output_path: Path, *, sc: Any) -> str:
    provider = config["provider"]
    model = config["model"]
    api_key = config["api_key"]
    def strip_embedded_tts_text(value: str) -> str:
        return re.sub(r"(?:朗读文本|正文|Text)\s*[:：]\s*[\s\S]*$", "", str(value or ""), flags=re.IGNORECASE).strip()
    def cosyvoice_instruction_from_prompt(value: str) -> str:
        instruction = strip_embedded_tts_text(value)
        instruction = re.sub(r"(?im)^\s*严格朗读当前\s+Scene\s+文本.*$", "", instruction)
        instruction = re.sub(r"(?im)^\s*不要朗读\s+prompt\s+中的示例文本或历史文本.*$", "", instruction)
        instruction = re.sub(r"\n{3,}", "\n\n", instruction).strip()
        if not instruction:
            return ""
        if "自然短视频口播" in instruction:
            return "普通话自然短视频口播；声音自然清晰，像自拍视频；中速平稳，重点词轻微强调；只朗读正文。"
        if "情绪" in instruction and ("轻重音" in instruction or "括号提示" in instruction):
            return "自然朗读正文，并执行正文中的情绪、停顿、轻重音或括号提示；说明性标签不读出。"
        if len(instruction) <= 96:
            return instruction
        compact = "；".join(line.strip("；。 ") for line in instruction.splitlines() if line.strip())
        if len(compact) > 96:
            compact = compact[:95].rstrip("，、；:： \n") + "。"
        return compact
    def should_retry_cosyvoice_without_instruction(error_detail: str) -> bool:
        value = str(error_detail or "").lower()
        return any(token in value for token in ("empty audio", "taskfailed", "task-failed", "invalidparameter", "428", "instruction"))
    if provider == "google":
        google_prompt = prompt or text_value
        if prompt and "{text}" not in prompt:
            google_prompt = f"{strip_embedded_tts_text(prompt) or prompt}\n\n正文：{text_value}"
        result = sc.post_json_request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}",
            {"contents": [{"parts": [{"text": google_prompt}]}], "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_id}}}}},
            {},
            timeout=60,
            error_prefix="TTS provider",
        )
        for candidate in result.get("candidates") or []:
            for part in (((candidate.get("content") or {}).get("parts")) or []):
                inline_data = part.get("inlineData") or part.get("inline_data") or {}
                encoded = str(inline_data.get("data") or "") if isinstance(inline_data, dict) else ""
                mime_type = str(inline_data.get("mimeType") or inline_data.get("mime_type") or "audio/wav") if isinstance(inline_data, dict) else "audio/wav"
                if encoded:
                    raw = base64.b64decode(encoded)
                    write_sanitized_audio_bytes(output_path, wav_data_from_pcm(raw, sample_rate=24000) if "pcm" in mime_type or "l16" in mime_type else raw)
                    return ""
        raise HTTPException(status_code=502, detail="Google TTS response did not include audio data")
    if provider == "xai":
        body, _content_type = sc.post_binary_request("https://api.x.ai/v1/tts", {"text": text_value, "voice_id": voice_id, "language": "zh", "format": "mp3"}, {"Authorization": f"Bearer {api_key}"}, timeout=60)
        if not body:
            raise HTTPException(status_code=502, detail="xAI TTS returned empty audio")
        write_sanitized_audio_bytes(output_path, body)
        return ""
    if provider == "bytedance":
        control_values = tts_prompt_control_values(prompt, text_value)
        bytedance_text = str(control_values.get("text") or text_value)
        bytedance_extra = dict(config.get("extra") or config)
        if control_values.get("speed_ratio") is not None:
            bytedance_extra["speed_ratio"] = control_values["speed_ratio"]
        if control_values.get("emotion"):
            bytedance_extra["enable_emotion"] = True
            bytedance_extra["emotion"] = control_values["emotion"]
            bytedance_extra["emotion_scale"] = int(control_values.get("emotion_scale") or 4)
        try:
            raw, _mime_type = bytedance_tts_audio_bytes(api_key, model, voice_id, bytedance_text, bytedance_extra)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        write_sanitized_audio_bytes(output_path, raw)
        return ""
    if provider == "heygen":
        try:
            result = sc.post_json_request(
                "https://api.heygen.com/v3/voices/speech",
                {"text": text_value, "voice_id": voice_id, "input_type": "text", "speed": 1, "language": "zh"},
                {"x-api-key": api_key},
                timeout=60,
                error_prefix="TTS provider",
            )
        except HTTPException as exc:
            detail = str(exc.detail)
            lowered = detail.lower()
            if "internal_error" in lowered or "http 500" in lowered:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=(
                        f"{detail}；HeyGen TTS 返回 5xx。克隆声音可能尚未可用于合成，"
                        "或 HeyGen 服务暂时异常。请稍后重试，或刷新声音列表后换一个已可用声音。"
                    ),
                ) from exc
            raise
        audio_url = first_audio_url(result)
        if not audio_url:
            raise HTTPException(status_code=502, detail="HeyGen TTS response did not include audio_url")
        sc.download_binary(audio_url, output_path, timeout=45)
        return audio_url
    if provider == "cosyvoice":
        instruction = cosyvoice_instruction_from_prompt(prompt)
        try:
            raw = dashscope_cosyvoice_tts_audio_bytes(
                api_key,
                model,
                voice_id,
                text_value,
                instruction,
                workspace=str(config.get("workspace") or config.get("workspace_id") or ""),
            )
        except Exception as exc:
            first_error = str(exc)
            if instruction and should_retry_cosyvoice_without_instruction(first_error):
                try:
                    raw = dashscope_cosyvoice_tts_audio_bytes(
                        api_key,
                        model,
                        voice_id,
                        text_value,
                        "",
                        workspace=str(config.get("workspace") or config.get("workspace_id") or ""),
                        max_attempts=1,
                    )
                except Exception as retry_exc:
                    raise HTTPException(
                        status_code=502,
                        detail=f"CosyVoice TTS failed after retry without instruction. First error: {first_error}; Retry error: {retry_exc}",
                    ) from retry_exc
            else:
                raise HTTPException(status_code=502, detail=f"CosyVoice TTS failed: {first_error}") from exc
        write_sanitized_audio_bytes(output_path, raw)
        return ""
    if provider == "qwen":
        input_payload = {"text": text_value, "voice": voice_id, "language_type": dashscope_language_type("zh")}
        if "instruct" in model and prompt.strip():
            instruction = strip_embedded_tts_text(prompt) or "自然中文短视频旁白，吐字清晰，节奏贴合画面。严格朗读 text 字段中的当前 Scene 文本，不要朗读示例文本或历史文本。"
            input_payload["instructions"] = instruction
            input_payload["optimize_instructions"] = True
        result = sc.post_json_request(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
            {"model": model, "input": input_payload},
            {"Authorization": f"Bearer {api_key}"},
            timeout=60,
            error_prefix="TTS provider",
        )
        audio_url = first_audio_url(result)
        if audio_url:
            sc.download_binary(audio_url, output_path, timeout=45)
            return audio_url
        audio_data = first_audio_data(result)
        if audio_data:
            write_sanitized_audio_bytes(output_path, base64.b64decode(audio_data))
            return ""
        raise HTTPException(status_code=502, detail=f"Qwen TTS response did not include audio url/data: {json.dumps(result, ensure_ascii=False)[:1000]}")
    if provider == "minimax":
        extra = dict(config.get("extra") or {})
        base_url = str(extra.get("base_url") or "https://api.minimaxi.com").rstrip("/")
        group_id = str(extra.get("group_id") or "").strip()
        if not group_id:
            raise HTTPException(status_code=400, detail="MiniMax TTS requires a GroupId in the provider extra config.")
        # The voice-clone config model is the clone model id; the actual T2A
        # synthesis needs a speech model (e.g. speech-02-hd) from extra.tts_model.
        speech_model = str(model or "").split("/")[-1]
        if not speech_model or speech_model.startswith("minimax-voice-clone"):
            speech_model = str(extra.get("tts_model") or "speech-02-hd")
        url = f"{base_url}/v1/t2a_v2?{urllib.parse.urlencode({'GroupId': group_id})}"
        result = sc.post_json_request(
            url,
            {
                "model": speech_model,
                "text": text_value,
                "voice_setting": {"voice_id": voice_id, "speed": 1, "vol": 1.0, "pitch": 0},
                "audio_setting": {"sample_rate": 32000, "format": "mp3"},
                "output_format": "url",
            },
            {"Authorization": f"Bearer {api_key}"},
            timeout=60,
            error_prefix="TTS provider",
        )
        base_resp = result.get("base_resp") if isinstance(result.get("base_resp"), dict) else {}
        status_code = base_resp.get("status_code")
        if status_code not in (None, 0, "0"):
            raise HTTPException(status_code=502, detail=f"MiniMax TTS failed: status_code={status_code}: {base_resp.get('status_msg') or 'unknown error'}")
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        audio_url = first_audio_url(data) or str(data.get("audio") or "").strip()
        if not audio_url:
            raise HTTPException(status_code=502, detail="MiniMax TTS response did not include data.audio url")
        sc.download_binary(audio_url, output_path, timeout=45)
        return audio_url
    raise HTTPException(status_code=400, detail=f"Unsupported StoryBoard TTS provider: {provider}/{model}")


def register_media_tts_provider_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
