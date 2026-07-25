from __future__ import annotations

import argparse
import base64
import html
import io
import json
import math
import os
import re
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


TOOL_ID = "03_02_ShotPlan_GTTSVoiceBuilder"
TOOL_NAME = TOOL_ID
TOOL_VERSION = "1.0.0"
DEFAULT_TTS_PROVIDER = "google"
DEFAULT_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_VOICES = ["Aoede", "Kore", "Callirrhoe", "Vindemiatrix", "Sulafat", "Achernar"]
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
CONFIG_TABLE = "tool_media_provider_configs"
TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
from opencrew_runtime_secrets import apply_provider_proxy, resolve_secret_value
REQUIRES = ["rebuild_shot_plan.json", "source_package.json", "session_reference_audio"]
OPTIONAL_INPUTS = ["tts/tts_reference_audio_manifest.json"]
PRODUCES = [
    "tts/gtts_voice_builder/gtts_voice_builder_manifest.json",
    "tts/gtts_voice_builder/gtts_voice_builder_review.html",
    "tts/gtts_voice_builder/gtts_voice_builder_selection.json",
    "tts/tts_voice_recommendations.json",
    f"reports/rebuild_v1/{TOOL_ID}.json",
]
SUGGESTED_PREVIOUS_TOOLS = ["02_Rebuild_ShotPlanBuilder", "03_01_ShotPlan_TTSReferenceAudioExtract"]
SUGGESTED_NEXT_TOOLS = ["03_03_ShotPlan_TTSVoiceSelectionWrite"]


class ToolError(RuntimeError):
    pass


@dataclass
class Candidate:
    candidate_id: str
    voice: str
    prompt: str
    round_index: int
    parent_id: str = ""
    note: str = ""


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
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_") or "item"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def rel(workspace: Path, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(workspace.resolve()))
    except Exception:
        return str(path)


def resolve_workspace_path(workspace: Path, value: str) -> Path:
    path = Path(str(value or "")).expanduser()
    return path if path.is_absolute() else workspace / path


def find_binary(name: str) -> str:
    candidates = [
        repo_root() / "OpenCrew" / ".bin" / name,
        repo_root() / "OpenCrew" / "ToolLibrary" / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
        repo_root() / "OpenCrew" / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which(name) or name


def run_cmd(cmd: list[str], timeout: int = 300) -> str:
    result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ToolError((result.stderr or result.stdout or " ".join(cmd))[-3000:])
    return result.stdout.strip()


def run_cmd_bytes(cmd: list[str], timeout: int = 300) -> bytes:
    result = subprocess.run(cmd, check=False, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b" ".join(item.encode("utf-8", errors="replace") for item in cmd))[-3000:]
        raise ToolError(detail.decode("utf-8", errors="replace"))
    return result.stdout


def media_duration(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        value = run_cmd(
            [find_binary("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            timeout=30,
        )
        return round(float(value), 3)
    except Exception:
        return 0.0


def plain_srt_text(value: str) -> str:
    rows: list[str] = []
    for line in str(value or "").splitlines():
        text = line.strip()
        if not text or text.isdigit() or "-->" in text:
            continue
        rows.append(text)
    return re.sub(r"\s+", " ", "".join(rows)).strip()


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("shots", []) if isinstance(item, dict)]


def shot_id_of(shot: dict[str, Any]) -> str:
    return str(shot.get("shot_id") or shot.get("id") or "").strip()


def shot_duration(shot: dict[str, Any]) -> float:
    for key in ("duration", "duration_seconds"):
        try:
            value = float(shot.get(key) or 0)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    try:
        value = float(reference.get("duration") or 0)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    return 0.0


def shot_text(shot: dict[str, Any]) -> str:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    for value in (
        reference.get("srt_text"),
        reference.get("spoken_script"),
        reference.get("spoken_text"),
        shot.get("srt_text"),
        shot.get("spoken_script"),
        shot.get("spoken_text"),
        shot.get("voiceover"),
    ):
        if isinstance(value, str) and value.strip():
            return plain_srt_text(value)
    return ""


def number_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def scene_mark_text(mark: dict[str, Any]) -> str:
    for key in ("srt_text", "source_srt_text", "original_srt_text", "spoken_script", "spoken_text", "text"):
        value = mark.get(key)
        if isinstance(value, str) and value.strip():
            return plain_srt_text(value)
    return ""


def srt_timestamp(seconds: float) -> str:
    clean = max(0.0, float(seconds or 0.0))
    hours = int(clean // 3600)
    minutes = int((clean % 3600) // 60)
    whole_seconds = int(clean % 60)
    millis = int(round((clean - int(clean)) * 1000))
    if millis >= 1000:
        whole_seconds += 1
        millis -= 1000
    if whole_seconds >= 60:
        minutes += 1
        whole_seconds -= 60
    if minutes >= 60:
        hours += 1
        minutes -= 60
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


def timed_text_rows(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for shot in shot_list(plan):
        reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
        shot_start = number_value(reference.get("start", shot.get("start")), 0.0)
        shot_end = number_value(reference.get("end", shot.get("end")), 0.0)
        shot_len = shot_duration(shot) or max(0.0, shot_end - shot_start)
        marks = reference.get("scene_marks") or shot.get("scene_marks") or []
        if isinstance(marks, list) and marks:
            for mark in marks:
                if not isinstance(mark, dict):
                    continue
                text = scene_mark_text(mark)
                if not text:
                    continue
                local_start = number_value(mark.get("start"), 0.0)
                local_end = number_value(mark.get("end"), local_start + number_value(mark.get("duration"), 0.0))
                if local_end <= local_start:
                    local_end = local_start + number_value(mark.get("duration"), 0.0)
                is_absolute = shot_start > 0 and shot_end > shot_start and local_start >= shot_start - 0.05 and local_end <= shot_end + 0.05
                start = local_start if is_absolute else shot_start + local_start
                end = local_end if is_absolute else shot_start + local_end
                if end <= start:
                    continue
                rows.append({
                    "shot_id": shot_id_of(shot),
                    "scene_mark_id": str(mark.get("scene_mark_id") or mark.get("scene_id") or "").strip(),
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(end - start, 3),
                    "text": text,
                    "source": "scene_mark",
                })
            continue
        text = shot_text(shot)
        if not text:
            continue
        end = shot_end if shot_end > shot_start else shot_start + shot_len
        rows.append({
            "shot_id": shot_id_of(shot),
            "scene_mark_id": "",
            "start": round(shot_start, 3),
            "end": round(end, 3),
            "duration": round(max(0.0, end - shot_start), 3),
            "text": text,
            "source": "shot",
        })
    return rows


def srt_for_text_window(rows: list[dict[str, Any]], clip_start: float, clip_end: float) -> str:
    lines: list[str] = []
    for index, row in enumerate(rows, 1):
        overlap_start = max(clip_start, number_value(row.get("start"), 0.0))
        overlap_end = min(clip_end, number_value(row.get("end"), clip_end))
        if overlap_end <= overlap_start:
            continue
        lines.extend([
            str(index),
            f"{srt_timestamp(overlap_start - clip_start)} --> {srt_timestamp(overlap_end - clip_start)}",
            str(row.get("text") or "").strip(),
            "",
        ])
    return "\n".join(lines).strip()


def sample_text_for_time_range(plan: dict[str, Any], reference_start: float, reference_duration: float) -> tuple[str, list[dict[str, Any]], str]:
    clip_start = max(0.0, float(reference_start or 0.0))
    clip_duration = max(0.0, float(reference_duration or 0.0))
    clip_end = clip_start + clip_duration
    rows: list[dict[str, Any]] = []
    for row in timed_text_rows(plan):
        row_start = number_value(row.get("start"), 0.0)
        row_end = number_value(row.get("end"), row_start)
        if row_end <= clip_start or row_start >= clip_end:
            continue
        overlap_start = max(clip_start, row_start)
        overlap_end = min(clip_end, row_end)
        rows.append({
            **row,
            "selection_start": round(clip_start, 3),
            "selection_end": round(clip_end, 3),
            "overlap_start": round(overlap_start, 3),
            "overlap_end": round(overlap_end, 3),
            "overlap_duration": round(max(0.0, overlap_end - overlap_start), 3),
        })
    if rows:
        return "".join(str(item["text"]) for item in rows).strip(), rows, srt_for_text_window(rows, clip_start, clip_end)
    text, fallback_rows = sample_text_for_duration(plan, clip_duration)
    return text, fallback_rows, ""


def sample_text_for_duration(plan: dict[str, Any], target_duration: float) -> tuple[str, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    total = 0.0
    for shot in shot_list(plan):
        text = shot_text(shot)
        duration = shot_duration(shot)
        if not text:
            continue
        rows.append({"shot_id": shot_id_of(shot), "duration": duration, "text": text})
        total += duration if duration > 0 else 0.0
        if total >= target_duration:
            break
    if not rows:
        fallback = " ".join(filter(None, (shot_text(shot) for shot in shot_list(plan)))).strip()
        return fallback, []
    return "".join(str(item["text"]) for item in rows).strip(), rows


def source_analysis_workspace(source_package: dict[str, Any]) -> Path | None:
    source = source_package.get("source") if isinstance(source_package.get("source"), dict) else {}
    value = source.get("analysis_workspace")
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser()
    for key in ("analysis_workspace", "source_workspace", "workspace"):
        value = source_package.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser()
    return None


def load_reference_audio_manifest(workspace: Path) -> dict[str, Any]:
    for path in (
        workspace / "tts" / "tts_reference_audio_manifest.json",
        workspace / "reports" / "rebuild_v1" / "03_01_ShotPlan_TTSReferenceAudioExtract.json",
    ):
        if not path.exists():
            continue
        try:
            payload = read_json(path)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def resolve_reference_audio(workspace: Path, source_package: dict[str, Any], manifest: dict[str, Any], explicit: str = "") -> tuple[Path | None, str, list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    if explicit.strip():
        candidates.append({"source": "cli.reference_audio", "path": explicit.strip()})
    reference_audio = manifest.get("reference_audio") if isinstance(manifest.get("reference_audio"), dict) else {}
    value = reference_audio.get("path")
    if isinstance(value, str) and value.strip():
        candidates.append({"source": reference_audio.get("source") or "tts_reference_audio_manifest", "path": value.strip()})
    analysis_workspace = source_analysis_workspace(source_package)
    if analysis_workspace:
        for relative in ("audio/reference_audio.wav", "outbox/reference_audio.wav", "audio/asr_enhanced_audio.wav", "audio/original_audio.wav"):
            candidates.append({"source": f"source.analysis_workspace/{relative}", "path": str(analysis_workspace / relative)})
    for item in candidates:
        path = Path(str(item.get("path") or "")).expanduser()
        item["exists"] = path.exists()
        if path.exists() and path.is_file():
            return path.resolve(), str(item.get("source") or ""), candidates
    return None, "", candidates


def extract_reference_clip(source_audio: Path, output_audio: Path, start: float, duration: float, force: bool = False) -> dict[str, Any]:
    if output_audio.exists() and output_audio.stat().st_size > 0 and not force:
        return {"source_audio": str(source_audio), "clip_audio": str(output_audio), "start": start, "duration": media_duration(output_audio), "cached": True}
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            find_binary("ffmpeg"),
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source_audio),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_audio),
        ],
        timeout=120,
    )
    return {"source_audio": str(source_audio), "clip_audio": str(output_audio), "start": start, "duration": media_duration(output_audio), "cached": False}


def normalize_database_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def resolve_database_url(args: argparse.Namespace) -> str:
    env_name = str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)
    return str(args.database_url or "") or os.environ.get(env_name, "") or os.environ.get("DATABASE_URL", "") or DEFAULT_OPENCREW_DATABASE_URL


def decode_db_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8", errors="replace").strip()
    return str(value or "").strip()


def load_google_tts_key(args: argparse.Namespace) -> str:
    env_key = os.environ.get("OPENCREW_TTS_API_KEY", "").strip()
    env_provider = os.environ.get("OPENCREW_TTS_PROVIDER", "").strip()
    if env_key and env_provider in {"", "google", "gemini"}:
        apply_provider_proxy("gemini")
        return env_key
    try:
        import psycopg  # type: ignore
    except Exception as exc:
        raise ToolError("PostgreSQL driver psycopg is not available and OPENCREW_TTS_API_KEY is not set") from exc
    sql = f"""
SELECT api_key_ref, api_key_ciphertext
FROM {CONFIG_TABLE}
WHERE kind = 'tts' AND provider IN ('google', 'gemini') AND enabled = TRUE
ORDER BY active DESC
LIMIT 1
"""
    with psycopg.connect(normalize_database_url(resolve_database_url(args)), connect_timeout=8) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
    if not row:
        raise ToolError("No enabled Google/Gemini TTS API key found in tool_media_provider_configs")
    api_key = resolve_secret_value(decode_db_value(row[0]), decode_db_value(row[1] if len(row) > 1 else ""))
    if not api_key:
        raise ToolError("No enabled Google/Gemini TTS API key found in local secret store")
    apply_provider_proxy("gemini")
    return api_key


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


def post_json(url: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(1, 4):
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                body = res.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:3000]
            raise ToolError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 * attempt)
                continue
    raise ToolError(f"URL error: {last_error}") from last_error


def generate_gemini_tts(api_key: str, model: str, voice: str, prompt: str, output_path: Path) -> dict[str, Any]:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
    response = post_json(url, payload, timeout=120)
    for candidate in response.get("candidates") or []:
        content = candidate.get("content") if isinstance(candidate, dict) else {}
        for part in (content.get("parts") or []):
            inline = part.get("inlineData") or part.get("inline_data") or {}
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
    raise ToolError(f"Gemini TTS did not return audio: {json.dumps(response, ensure_ascii=False)[:1500]}")


def load_audio_mono(path: Path, target_rate: int = 16000) -> tuple[Any, int]:
    import numpy as np  # type: ignore

    raw = run_cmd_bytes(
        [
            find_binary("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(target_rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        timeout=120,
    )
    y = np.frombuffer(raw, dtype="<f4").astype(np.float64)
    if y.size == 0:
        raise ToolError(f"Empty audio: {path}")
    peak = float(np.max(np.abs(y))) or 1.0
    y = y / peak
    return y.astype(np.float64), target_rate


def trim_voice(y: Any, sample_rate: int = 16000, threshold: float = 0.025) -> Any:
    import numpy as np  # type: ignore

    frame = max(1, int(0.025 * sample_rate))
    hop = max(1, int(0.010 * sample_rate))
    if y.size <= frame:
        return y
    rms_values = []
    for start in range(0, y.size - frame + 1, hop):
        seg = y[start : start + frame]
        rms_values.append(float(np.sqrt(np.mean(seg * seg))))
    active = [idx for idx, value in enumerate(rms_values) if value > threshold]
    if not active:
        return y
    start = max(0, active[0] * hop)
    end = min(y.size, active[-1] * hop + frame)
    return y[start:end]


def hz_to_mel(value: float) -> float:
    return 2595 * math.log10(1 + value / 700)


def compact_mfcc_like(y: Any, sample_rate: int) -> list[float]:
    import numpy as np  # type: ignore

    frame = int(0.025 * sample_rate)
    hop = int(0.010 * sample_rate)
    nfft = 512
    if y.size < frame:
        y = np.pad(y, (0, frame - y.size))
    frames = [y[start : start + frame] * np.hamming(frame) for start in range(0, y.size - frame + 1, hop)]
    if not frames:
        frames = [np.pad(y, (0, max(0, frame - y.size)))[:frame] * np.hamming(frame)]
    power = np.abs(np.fft.rfft(np.vstack(frames), n=nfft)) ** 2
    nfilt = 24
    mel_points = np.linspace(hz_to_mel(80), hz_to_mel(sample_rate / 2), nfilt + 2)
    bins = np.floor((nfft + 1) * (700 * (np.power(10, mel_points / 2595) - 1)) / sample_rate).astype(int)
    filters = np.zeros((nfilt, nfft // 2 + 1))
    for j in range(nfilt):
        left, center, right = bins[j], bins[j + 1], bins[j + 2]
        for i in range(left, center):
            if 0 <= i < filters.shape[1]:
                filters[j, i] = (i - left) / max(1, center - left)
        for i in range(center, right):
            if 0 <= i < filters.shape[1]:
                filters[j, i] = (right - i) / max(1, right - center)
    energies = np.dot(power, filters.T)
    logs = np.log(np.where(energies <= 0, 1e-10, energies))
    coeff_count = 13
    basis = np.zeros((coeff_count, nfilt))
    for k in range(coeff_count):
        basis[k, :] = np.cos(math.pi * k * (np.arange(nfilt) + 0.5) / nfilt)
    coeffs = np.dot(logs, basis.T)
    stats = np.concatenate([coeffs.mean(axis=0), coeffs.std(axis=0)])
    return [float(item) for item in stats]


def pitch_estimate(y: Any, sample_rate: int) -> dict[str, float]:
    import numpy as np  # type: ignore

    frame = int(0.04 * sample_rate)
    hop = int(0.01 * sample_rate)
    lo = max(1, int(sample_rate / 500))
    hi = max(lo + 1, int(sample_rate / 60))
    values: list[float] = []
    frame_count = 0
    for start in range(0, max(1, y.size - frame), hop):
        frame_count += 1
        segment = y[start : start + frame]
        if segment.size < frame or float(np.sqrt(np.mean(segment * segment))) < 0.025:
            continue
        segment = segment * np.hamming(segment.size)
        corr = np.correlate(segment, segment, mode="full")[segment.size - 1 :]
        if corr[0] <= 0:
            continue
        search = corr[lo:hi]
        if search.size == 0:
            continue
        lag = int(np.argmax(search)) + lo
        confidence = float(corr[lag] / corr[0])
        hz = sample_rate / lag
        if confidence > 0.25 and 60 < hz < 500:
            values.append(hz)
    if not values:
        return {"median": 0.0, "std": 0.0, "variation_semitones": 0.0, "voiced_ratio": 0.0}
    arr = np.array(values)
    median = float(np.median(arr))
    semitones = 12 * np.log2(arr / max(1e-6, median))
    return {
        "median": median,
        "std": float(np.std(arr)),
        "variation_semitones": float(np.std(semitones)),
        "voiced_ratio": float(len(values) / max(1, frame_count)),
    }


def audio_features(path: Path) -> dict[str, Any]:
    import numpy as np  # type: ignore

    original_duration = media_duration(path)
    y, rate = load_audio_mono(path, 16000)
    duration = original_duration or (y.size / rate)
    y = y - np.mean(y)
    peak = float(np.max(np.abs(y))) or 1.0
    y = y / peak
    active = trim_voice(y, rate)
    active_duration = active.size / rate
    rms = float(np.sqrt(np.mean(active * active))) if active.size else 0.0
    spectrum = np.abs(np.fft.rfft(active * np.hamming(active.size))) if active.size else np.array([0.0])
    freqs = np.fft.rfftfreq(active.size, 1 / rate) if active.size else np.array([0.0])
    centroid = float((freqs * spectrum).sum() / (spectrum.sum() + 1e-9))
    f0 = pitch_estimate(active, rate)
    active_ratio = active_duration / max(0.1, duration)
    signal_confidence = max(0.0, min(1.0, 0.45 * min(1.0, active_ratio) + 0.35 * f0["voiced_ratio"] + 0.20 * min(1.0, rms / 0.12)))
    return {
        "duration": duration,
        "active_duration": active_duration,
        "active_ratio": active_ratio,
        "signal_confidence": signal_confidence,
        "rms": rms,
        "centroid": centroid,
        "f0_median": f0["median"],
        "f0_std": f0["std"],
        "f0_variation_semitones": f0["variation_semitones"],
        "voiced_ratio": f0["voiced_ratio"],
        "mfcc": compact_mfcc_like(active, rate),
    }


def cosine_similarity(left: list[float], right: list[float]) -> float:
    import numpy as np  # type: ignore

    a = np.array(left, dtype=np.float64)
    b = np.array(right, dtype=np.float64)
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def exp_similarity(left: float, right: float, scale: float) -> float:
    return math.exp(-abs(left - right) / scale)


def pitch_similarity(reference: dict[str, Any], candidate: dict[str, Any]) -> float:
    f0 = exp_similarity(float(reference["f0_median"]), float(candidate["f0_median"]), 75)
    variation = exp_similarity(float(reference["f0_variation_semitones"]), float(candidate["f0_variation_semitones"]), 2.5)
    return 0.75 * f0 + 0.25 * variation


def score_candidate(reference: dict[str, Any], candidate: dict[str, Any], target_duration: float) -> tuple[float, dict[str, float]]:
    timbre = (cosine_similarity(reference["mfcc"], candidate["mfcc"]) + 1.0) / 2.0
    pitch = pitch_similarity(reference, candidate)
    brightness = exp_similarity(float(reference["centroid"]), float(candidate["centroid"]), 500)
    energy = exp_similarity(float(reference["rms"]), float(candidate["rms"]), 0.10)
    rhythm = exp_similarity(float(reference["active_ratio"]), float(candidate["active_ratio"]), 0.18)
    duration = exp_similarity(target_duration, float(candidate["duration"]), 1.2)
    expressiveness = exp_similarity(float(reference["f0_variation_semitones"]), float(candidate["f0_variation_semitones"]), 1.8)
    score = (
        0.30 * timbre
        + 0.23 * pitch
        + 0.12 * brightness
        + 0.07 * energy
        + 0.12 * rhythm
        + 0.11 * duration
        + 0.05 * expressiveness
    )
    duration_penalty = 1.0
    if candidate["duration"] > target_duration * 1.55:
        duration_penalty *= 0.55
    if candidate["duration"] < target_duration * 0.55:
        duration_penalty *= 0.70
    score *= duration_penalty
    parts = {
        "timbre": timbre,
        "pitch": pitch,
        "brightness": brightness,
        "energy": energy,
        "rhythm": rhythm,
        "duration": duration,
        "expressiveness": expressiveness,
        "duration_penalty": duration_penalty,
        "raw_duration": float(candidate["duration"]),
        "f0_median": float(candidate["f0_median"]),
        "centroid": float(candidate["centroid"]),
        "rms": float(candidate["rms"]),
        "active_ratio": float(candidate["active_ratio"]),
    }
    return score, parts


def summarize_features(features: dict[str, Any]) -> dict[str, float]:
    return {
        key: round(float(features.get(key) or 0.0), 4)
        for key in ("duration", "active_duration", "active_ratio", "signal_confidence", "rms", "centroid", "f0_median", "f0_variation_semitones", "voiced_ratio")
    }


def initial_candidates(text: str, voices: list[str], limit: int) -> list[Candidate]:
    prompt_styles = [
        ("text_only", "{text}", "baseline: let Gemini speak the text without instructions"),
        (
            "home_natural",
            "请用普通话朗读下面中文正文，只朗读正文，不要读出说明。声音像一位年轻中国女性在家里自然分享产品，轻快、亲近、真实，语速略快，句尾带自然的“啊”感：\n{text}",
            "young female Mandarin, home product sharing, close-mic, natural short-video delivery",
        ),
        (
            "soft_bright",
            "Speak only the Mandarin text below. Use a young female voice: bright, soft, casual, close-mic, natural short-video delivery, slightly fast pace, warm home-product sharing tone.\n{text}",
            "bright and soft young female voice",
        ),
        (
            "clear_lively",
            "请只说这段话：{text}\n语气要求：年轻女性，清晰、明亮、生活化，不像播音腔，不要过度甜，像在家里给朋友安利产品，节奏紧凑但自然。",
            "clear lively non-broadcast delivery",
        ),
        (
            "breathy_warm",
            "请以年轻女性自然中文口播读出正文，不读提示。音色温暖偏亮、轻微气声、近距离收音，语气亲切但不过分表演，停顿短：{text}",
            "warm breathy close-mic delivery",
        ),
        (
            "urgent_daily",
            "只朗读正文。用年轻妈妈/妻子生活分享的中文口吻，语速偏快，带一点日常强调和句尾上扬，声音清透自然：{text}",
            "daily-life slightly urgent delivery",
        ),
    ]
    rows: list[Candidate] = []
    for style_id, template, note in prompt_styles:
        for voice in voices:
            if len(rows) >= limit:
                return rows
            rows.append(Candidate(f"r1_{safe_name(voice)}_{style_id}", voice, template.format(text=text), 1, note=note))
    return rows


def prompt_template_from_prompt(prompt: str, text: str) -> str:
    if text and text in prompt:
        return prompt.replace(text, "{text}", 1)
    return "{text}"


def refined_prompt(parent: dict[str, Any], reference: dict[str, Any], text: str, round_index: int, variant: int) -> str:
    parts = parent.get("score_parts") or {}
    duration_ratio = float(parts.get("raw_duration") or reference["duration"]) / max(0.1, float(reference["duration"]))
    f0_delta = float(parts.get("f0_median") or 0) - float(reference["f0_median"])
    bright_delta = float(parts.get("centroid") or 0) - float(reference["centroid"])
    active_delta = float(parts.get("active_ratio") or 0) - float(reference["active_ratio"])
    instructions: list[str] = ["只朗读正文，不读提示", "年轻中国女性", "生活短视频口播", "近距离自然收音"]
    if duration_ratio > 1.15:
        instructions.append("语速更快一点，停顿更短")
    elif duration_ratio < 0.88:
        instructions.append("语速稍慢一点，句子更从容")
    else:
        instructions.append("保持原片紧凑节奏")
    if f0_delta < -25:
        instructions.append("音调略高更年轻")
    elif f0_delta > 25:
        instructions.append("音调略低更稳")
    else:
        instructions.append("中高音区，女声明亮但不尖")
    if bright_delta > 450:
        instructions.append("音色更柔和，减少刺亮感")
    elif bright_delta < -450:
        instructions.append("音色更清亮，口腔共鸣更靠前")
    else:
        instructions.append("清透温暖")
    if active_delta < -0.08:
        instructions.append("减少长停顿")
    if variant == 2:
        instructions.append("情绪更亲切，像给家人准备东西时顺口说明")
    if variant == 3:
        instructions.append("更像手机自拍视频里的真实说话，不要播音腔")
    return f"请按以下声音方向朗读，且只输出正文语音：{'; '.join(instructions)}。\n正文：{text}"


def atempo_chain(tempo: float) -> str:
    values: list[float] = []
    current = max(0.01, tempo)
    while current > 2.0:
        values.append(2.0)
        current /= 2.0
    while current < 0.5:
        values.append(0.5)
        current /= 0.5
    values.append(current)
    return ",".join(f"atempo={value:.6f}" for value in values)


def enforce_exact_duration(path: Path, target_duration: float) -> None:
    try:
        with wave.open(str(path), "rb") as reader:
            params = reader.getparams()
            frames = reader.readframes(params.nframes)
    except wave.Error:
        tmp_path = path.with_name(f"{path.stem}.duration_tmp{path.suffix}")
        run_cmd(
            [
                find_binary("ffmpeg"),
                "-y",
                "-i",
                str(path),
                "-af",
                f"apad=pad_dur={target_duration:.6f},atrim=duration={target_duration:.6f},asetpts=N/SR/TB",
                str(tmp_path),
            ],
            timeout=120,
        )
        tmp_path.replace(path)
        return

    target_frames = max(1, int(round(target_duration * params.framerate)))
    bytes_per_frame = params.nchannels * params.sampwidth
    if params.nframes < target_frames:
        frames += b"\0" * ((target_frames - params.nframes) * bytes_per_frame)
    elif params.nframes > target_frames:
        frames = frames[: target_frames * bytes_per_frame]

    with wave.open(str(path), "wb") as writer:
        writer.setparams(params._replace(nframes=target_frames))
        writer.writeframes(frames)


def fit_audio_to_duration(input_audio: Path, output_audio: Path, target_duration: float) -> dict[str, float]:
    raw_duration = media_duration(input_audio) or target_duration
    tempo = raw_duration / target_duration if target_duration > 0 else 1.0
    filters = [
        "aresample=48000",
        "aformat=channel_layouts=stereo",
        atempo_chain(tempo),
        "loudnorm=I=-17:LRA=11:TP=-1.5",
        f"apad=pad_dur={target_duration:.6f},atrim=duration={target_duration:.6f}",
        "asetpts=N/SR/TB",
    ]
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    run_cmd([find_binary("ffmpeg"), "-y", "-i", str(input_audio), "-af", ",".join(filters), "-ar", "48000", "-ac", "2", str(output_audio)], timeout=240)
    enforce_exact_duration(output_audio, target_duration)
    return {"raw_duration": raw_duration, "target_duration": target_duration, "tempo": tempo, "fit_duration": media_duration(output_audio)}


def build_html_review(workspace: Path, output_path: Path, manifest: dict[str, Any], selection_path: Path) -> None:
    candidates = manifest.get("top_candidates") if isinstance(manifest.get("top_candidates"), list) else []
    rows = []
    for index, candidate in enumerate(candidates, start=1):
        audio = rel(output_path.parent, candidate.get("audio") or candidate.get("raw_audio") or "")
        prompt = html.escape(str(candidate.get("prompt") or ""))
        parts = candidate.get("score_parts") if isinstance(candidate.get("score_parts"), dict) else {}
        parts_rows = "".join(
            f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(str(value))}</td></tr>"
            for key, value in parts.items()
        )
        rows.append(
            f"""
<section class="candidate" data-candidate-id="{html.escape(str(candidate.get('candidate_id') or ''))}">
  <div class="rank">#{index}</div>
  <div class="main">
    <h2>{html.escape(str(candidate.get('voice') or ''))} <span>{html.escape(str(candidate.get('score') or ''))}</span></h2>
    <p class="meta">Model: {html.escape(str(candidate.get('model') or ''))} · Raw: {html.escape(str(candidate.get('raw_duration') or ''))}s · Fitted: {html.escape(str(candidate.get('fit_duration') or ''))}s</p>
    <audio controls src="{html.escape(audio)}"></audio>
    <label><input type="radio" name="defaultCandidate" value="{html.escape(str(candidate.get('candidate_id') or ''))}" {'checked' if index == 1 else ''}> Use as default</label>
    <h3>Prompt</h3>
    <pre>{prompt}</pre>
    <h3>Score Parts</h3>
    <table>{parts_rows}</table>
  </div>
</section>
"""
        )
    candidates_json = json.dumps(candidates, ensure_ascii=False)
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gemini TTS Voice Builder Review</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f6f7f9; color: #1f2933; }}
    header {{ padding: 24px 32px; background: #ffffff; border-bottom: 1px solid #d9dee7; position: sticky; top: 0; z-index: 2; }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    .summary {{ margin: 0; color: #53606f; font-size: 13px; }}
    .actions {{ display: flex; gap: 10px; margin-top: 14px; align-items: center; flex-wrap: wrap; }}
    button {{ border: 1px solid #1f6feb; background: #1f6feb; color: white; border-radius: 6px; padding: 8px 12px; font-size: 13px; cursor: pointer; }}
    button.secondary {{ background: white; color: #1f2933; border-color: #c9d1dc; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 20px; }}
    .candidate {{ display: grid; grid-template-columns: 52px 1fr; gap: 16px; background: #fff; border: 1px solid #dce2ea; border-radius: 8px; padding: 18px; margin-bottom: 16px; }}
    .rank {{ width: 40px; height: 40px; border-radius: 20px; display: grid; place-items: center; background: #17202a; color: #fff; font-weight: 700; }}
    h2 {{ margin: 0; font-size: 18px; }}
    h2 span {{ color: #1f6feb; font-size: 15px; margin-left: 8px; }}
    h3 {{ margin: 18px 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: #53606f; }}
    .meta {{ margin: 6px 0 12px; color: #53606f; font-size: 13px; }}
    audio {{ width: 100%; max-width: 560px; display: block; margin-bottom: 10px; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #f3f5f8; border: 1px solid #dce2ea; border-radius: 6px; padding: 12px; font-size: 13px; line-height: 1.55; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 720px; font-size: 13px; }}
    td {{ border-bottom: 1px solid #e3e8ef; padding: 7px 8px; }}
    td:first-child {{ color: #53606f; width: 210px; }}
    textarea {{ width: 100%; min-height: 180px; margin-top: 16px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
  </style>
</head>
<body>
<header>
  <h1>Gemini TTS Voice Builder Review</h1>
  <p class="summary">Reference: {html.escape(str(manifest.get('reference_clip', {}).get('clip_audio') or ''))} · Model: {html.escape(str(manifest.get('model') or ''))}</p>
  <div class="actions">
    <button id="buildSelection">Build Selection JSON</button>
    <button id="downloadSelection" class="secondary">Download JSON</button>
    <span id="status"></span>
  </div>
</header>
<main>
{''.join(rows)}
<textarea id="selectionJson" spellcheck="false"></textarea>
</main>
<script>
const candidates = {candidates_json};
const selectionPath = {json.dumps(rel(workspace, selection_path), ensure_ascii=False)};
function selectedCandidate() {{
  const checked = document.querySelector('input[name="defaultCandidate"]:checked');
  const id = checked ? checked.value : (candidates[0] && candidates[0].candidate_id);
  return candidates.find(item => item.candidate_id === id) || candidates[0];
}}
function buildSelection() {{
  const selected = selectedCandidate();
  const payload = {{
    tool: {json.dumps(TOOL_ID)},
    tool_version: {json.dumps(TOOL_VERSION)},
    status: "confirmed_in_html",
    selection_path: selectionPath,
    selected_at: new Date().toISOString(),
    selected_candidate_id: selected ? selected.candidate_id : "",
    default_selection: selected || {{}},
    note: "Download this JSON or paste it back to the workflow if browser file writing is unavailable."
  }};
  document.getElementById('selectionJson').value = JSON.stringify(payload, null, 2);
  document.getElementById('status').textContent = 'Selection JSON ready';
  return payload;
}}
document.getElementById('buildSelection').addEventListener('click', buildSelection);
document.getElementById('downloadSelection').addEventListener('click', () => {{
  const payload = buildSelection();
  const blob = new Blob([JSON.stringify(payload, null, 2)], {{ type: 'application/json' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'gtts_voice_builder_selection.json';
  a.click();
  URL.revokeObjectURL(url);
}});
buildSelection();
</script>
</body>
</html>
"""
    write_text(output_path, html_text)


def build_compatible_recommendations(plan: dict[str, Any], manifest: dict[str, Any], selection: dict[str, Any]) -> dict[str, Any]:
    recommendations = []
    top_candidates = manifest.get("top_candidates") if isinstance(manifest.get("top_candidates"), list) else []
    for shot in shot_list(plan):
        recommendations.append(
            {
                "shot_id": shot_id_of(shot),
                "provider": DEFAULT_TTS_PROVIDER,
                "model": manifest.get("model") or DEFAULT_TTS_MODEL,
                "voice": selection.get("voice") or "",
                "label": selection.get("voice") or "",
                "score": selection.get("score"),
                "reason": "global Gemini TTS voice/prompt builder selection from 16s session reference audio",
                "match_source": "gtts_voice_builder",
                "prompt": selection.get("prompt") or "",
                "prompt_template": selection.get("prompt_template") or "{text}",
                "audio": selection.get("audio") or "",
                "fit_audio": selection.get("fit_audio") or selection.get("audio") or "",
                "raw_audio": selection.get("raw_audio") or "",
                "candidate_id": selection.get("candidate_id") or "",
                "top_candidates": top_candidates,
            }
        )
    return {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "generated_at": now_ms(),
        "status": "completed",
        "recommendations": recommendations,
        "match_result": {
            "provider": DEFAULT_TTS_PROVIDER,
            "model": manifest.get("model") or DEFAULT_TTS_MODEL,
            "reference_audio": manifest.get("reference_audio"),
            "reference_clip": manifest.get("reference_clip"),
            "top": top_candidates,
        },
        "warnings": [],
    }


def optimize(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    plan = read_json(workspace / args.input)
    source_package = read_json(workspace / args.source_package)
    manifest = load_reference_audio_manifest(workspace)
    reference_audio, reference_source, reference_candidates = resolve_reference_audio(workspace, source_package, manifest, args.reference_audio)
    if not reference_audio:
        raise ToolError("No session reference audio found. Run 03_01_ShotPlan_TTSReferenceAudioExtract first or pass --reference-audio.")
    output_dir = workspace / args.output_dir
    reference_clip_path = output_dir / "reference" / f"reference_{float(args.reference_start):.3f}_{float(args.reference_duration):.3f}s.wav"
    reference_clip = extract_reference_clip(reference_audio, reference_clip_path, float(args.reference_start), float(args.reference_duration), bool(args.force))
    text, text_sources, sample_srt = sample_text_for_time_range(plan, float(args.reference_start), float(args.reference_duration))
    if not text:
        raise ToolError("No TTS sample text found in rebuild_shot_plan.json")
    voices = [item.strip() for item in str(args.voices or "").split(",") if item.strip()] or DEFAULT_VOICES
    target_duration = media_duration(reference_clip_path) or float(args.reference_duration)
    reference_features = audio_features(reference_clip_path)
    api_key = load_google_tts_key(args)
    model = str(args.model or DEFAULT_TTS_MODEL)
    candidates = initial_candidates(text, voices, max(1, int(args.candidates_per_round)))
    all_rows: list[dict[str, Any]] = []
    best_row: dict[str, Any] | None = None
    for round_index in range(1, max(1, int(args.rounds)) + 1):
        round_dir = output_dir / f"round_{round_index:02d}"
        round_rows: list[dict[str, Any]] = []
        for candidate in candidates[: max(1, int(args.candidates_per_round))]:
            raw_path = round_dir / f"{candidate.candidate_id}.wav"
            started = time.time()
            error = ""
            try:
                if raw_path.exists() and media_duration(raw_path) > 0 and not args.force:
                    meta = {"mime_type": "audio/wav", "duration": media_duration(raw_path), "cached": True}
                else:
                    meta = generate_gemini_tts(api_key, model, candidate.voice, candidate.prompt, raw_path)
                features = audio_features(raw_path)
                score, parts = score_candidate(reference_features, features, target_duration)
                row = {
                    "candidate_id": candidate.candidate_id,
                    "round": round_index,
                    "parent_id": candidate.parent_id,
                    "provider": DEFAULT_TTS_PROVIDER,
                    "model": model,
                    "voice": candidate.voice,
                    "prompt": candidate.prompt,
                    "prompt_template": prompt_template_from_prompt(candidate.prompt, text),
                    "note": candidate.note,
                    "raw_audio": str(raw_path),
                    "gemini_meta": meta,
                    "features": summarize_features(features),
                    "score": round(float(score), 6),
                    "score_parts": {key: round(float(value), 6) for key, value in parts.items()},
                    "raw_duration": round(media_duration(raw_path), 3),
                    "elapsed_seconds": round(time.time() - started, 3),
                }
            except Exception as exc:
                error = str(exc)
                row = {
                    "candidate_id": candidate.candidate_id,
                    "round": round_index,
                    "parent_id": candidate.parent_id,
                    "provider": DEFAULT_TTS_PROVIDER,
                    "model": model,
                    "voice": candidate.voice,
                    "prompt": candidate.prompt,
                    "prompt_template": prompt_template_from_prompt(candidate.prompt, text),
                    "note": candidate.note,
                    "raw_audio": str(raw_path),
                    "score": 0.0,
                    "error": error[-2000:],
                    "elapsed_seconds": round(time.time() - started, 3),
                }
            round_rows.append(row)
            all_rows.append(row)
            if not error and (best_row is None or float(row["score"]) > float(best_row["score"])):
                best_row = row
        ranked = sorted([row for row in round_rows if not row.get("error")], key=lambda item: float(item["score"]), reverse=True)
        write_json(round_dir / "round_report.json", {"round": round_index, "ranked": ranked, "all": round_rows})
        if round_index >= int(args.rounds):
            break
        parents = ranked[: max(1, min(2, len(ranked)))]
        next_candidates: list[Candidate] = []
        for parent in parents:
            for variant in range(1, 4):
                cid = f"r{round_index + 1}_{safe_name(str(parent['voice']))}_{safe_name(str(parent['candidate_id']))}_v{variant}"
                next_candidates.append(
                    Candidate(
                        cid,
                        str(parent["voice"]),
                        refined_prompt(parent, reference_features, text, round_index + 1, variant),
                        round_index + 1,
                        parent_id=str(parent["candidate_id"]),
                        note="deterministic refinement from acoustic score deltas",
                    )
                )
        candidates = next_candidates
    successful = sorted([row for row in all_rows if not row.get("error")], key=lambda item: float(item.get("score") or 0), reverse=True)
    if not successful:
        raise ToolError("No successful Gemini TTS candidate was generated")
    top_candidates = []
    for row in successful[: max(1, int(args.top_k))]:
        fit_path = output_dir / "top_fitted" / f"{row['candidate_id']}_fit_{target_duration:.3f}s.wav"
        fit_meta = fit_audio_to_duration(Path(str(row["raw_audio"])), fit_path, target_duration)
        top_candidates.append(
            {
                **row,
                "audio": str(fit_path),
                "fit_audio": str(fit_path),
                "fit_meta": fit_meta,
                "fit_duration": round(float(fit_meta.get("fit_duration") or 0), 3),
            }
        )
    selection = top_candidates[0]
    selection_path = output_dir / "gtts_voice_builder_selection.json"
    selection_payload = {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "status": "default_top1_pending_html_confirmation",
        "generated_at": now_ms(),
        "selected_candidate_id": selection.get("candidate_id"),
        "default_selection": selection,
        "top_candidates": top_candidates,
    }
    write_json(selection_path, selection_payload)
    output_manifest = {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "generated_at": now_ms(),
        "status": "completed",
        "workspace": str(workspace),
        "provider": DEFAULT_TTS_PROVIDER,
        "model": model,
        "voices": voices,
        "reference_audio": str(reference_audio),
        "reference_source": reference_source,
        "reference_candidates": reference_candidates,
        "reference_clip": reference_clip,
        "reference_features": summarize_features(reference_features),
        "sample_text": text,
        "sample_srt": sample_srt,
        "sample_text_sources": text_sources,
        "target_duration": target_duration,
        "rounds": int(args.rounds),
        "candidates_per_round": int(args.candidates_per_round),
        "top_candidates": top_candidates,
        "all_candidates": sorted(all_rows, key=lambda item: float(item.get("score") or 0), reverse=True),
        "selection": selection_payload,
    }
    manifest_path = output_dir / "gtts_voice_builder_manifest.json"
    write_json(manifest_path, output_manifest)
    if args.generate_html:
        html_path = output_dir / "gtts_voice_builder_review.html"
        build_html_review(workspace, html_path, output_manifest, selection_path)
        output_manifest["html_review"] = str(html_path)
        write_json(manifest_path, output_manifest)
    compatible = build_compatible_recommendations(plan, output_manifest, selection)
    write_json(workspace / "tts" / "tts_voice_recommendations.json", compatible)
    write_json(workspace / "reports" / "rebuild_v1" / f"{TOOL_ID}.json", output_manifest)
    return output_manifest


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    satisfied: list[str] = []
    missing: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    plan_path = workspace / args.input
    source_path = workspace / args.source_package
    if plan_path.exists():
        satisfied.append("rebuild_shot_plan.json")
    else:
        missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"required workspace file does not exist: {args.input}", "suggested_tools": ["02_Rebuild_ShotPlanBuilder"]})
    source_package: dict[str, Any] = {}
    if source_path.exists():
        satisfied.append("source_package.json")
        try:
            payload = read_json(source_path)
            source_package = payload if isinstance(payload, dict) else {}
        except Exception as exc:
            missing.append({"dependency": "source_package.json", "reason": f"failed to read source package: {exc}", "suggested_tools": ["01_Rebuild_SourcePackageLoad"]})
    else:
        missing.append({"dependency": "source_package.json", "reason": f"required workspace file does not exist: {args.source_package}", "suggested_tools": ["01_Rebuild_SourcePackageLoad"]})
    manifest = load_reference_audio_manifest(workspace)
    reference_audio, _source, candidates = resolve_reference_audio(workspace, source_package, manifest, args.reference_audio)
    if reference_audio:
        satisfied.append("session_reference_audio")
    else:
        missing.append({"dependency": "session_reference_audio", "reason": "no reference audio found in tts manifest or source analysis workspace", "suggested_tools": ["03_01_ShotPlan_TTSReferenceAudioExtract"], "candidates": candidates})
    if not (workspace / "tts" / "tts_reference_audio_manifest.json").exists():
        warnings.append({"dependency": "tts/tts_reference_audio_manifest.json", "reason": "optional manifest is absent; tool will fall back to source_package.source.analysis_workspace/audio/reference_audio.wav"})
    for name in ("ffmpeg", "ffprobe"):
        binary = find_binary(name)
        if not shutil.which(binary) and not Path(binary).exists():
            missing.append({"dependency": name, "reason": f"required media binary not found: {name}", "suggested_tools": []})
        else:
            satisfied.append(name)
    return {"status": "blocked" if missing else "warning" if warnings else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": warnings}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--output-dir", default="tts/gtts_voice_builder")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--reference-audio", default="")
    parser.add_argument("--reference-start", type=float, default=0.0)
    parser.add_argument("--reference-duration", type=float, default=16.0)
    parser.add_argument("--model", default=DEFAULT_TTS_MODEL)
    parser.add_argument("--voices", default=",".join(DEFAULT_VOICES))
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--candidates-per-round", type=int, default=6)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--generate-html", dest="generate_html", action="store_true", default=True)
    parser.add_argument("--no-generate-html", dest="generate_html", action="store_false")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    dependencies = check_dependencies(workspace, args)
    try:
        if args.check_dependencies_only or (dependencies["missing"] and not args.force):
            status, result = ("blocked" if dependencies["missing"] else "completed_with_warnings" if dependencies["warnings"] else "completed"), None
        else:
            result = optimize(workspace, args)
            status = result.get("status", "completed")
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
