from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    np = None  # type: ignore

VIDEO_ANALYSIS_ROOT = Path(os.environ.get("ANALYSIS_V1_VIDEO_ANALYSIS_ROOT", str(Path.home() / "Development/OpenCode/VideoAnalysis"))).expanduser()
RESEMBLYZER_ROOT = VIDEO_ANALYSIS_ROOT / "Resemblyzer"
SPEECHBRAIN_ROOT = VIDEO_ANALYSIS_ROOT / "speechbrain"
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))

try:
    from ToolLibrary.Analysis_V1.provider_audit import record_model_call_audit
except ModuleNotFoundError:
    from OpenCrew.ToolLibrary.Analysis_V1.provider_audit import record_model_call_audit


TOOL_NAME = "03_02_TTSBuilderQuick"
TOOL_VERSION = "0.1.1"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
TOOL_DIR_NAME = "S5_03_02_TTSBuilderQuick"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_FINAL_ITEMS_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_4_final_srt_frame_items.json"
WORKING_REFERENCE_REL = f"{TOOL_DIR_NAME}/Working/Audio_Reference_Selected.wav"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
WORKING_RAW_DIR_REL = f"{TOOL_DIR_NAME}/Working/raw_candidates"
WORKING_FIT_DIR_REL = f"{TOOL_DIR_NAME}/Working/fitted_candidates"
OUTPUT_REFERENCE_PROFILE_REL = f"{TOOL_DIR_NAME}/Output/reference_voice_profile.json"
OUTPUT_CATALOG_AUDIT_REL = f"{TOOL_DIR_NAME}/Output/catalog_match_audit.json"
OUTPUT_FINAL_REL = f"{TOOL_DIR_NAME}/Output/tts_builder_candidates.json"
PROMPT_DIR_REL = f"{TOOL_DIR_NAME}/Prompt"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SESSION_FINAL_ITEMS_REL = "SessionOutput/subtitle/final_srt_frame_items.json"
SESSION_AUDIO_REFERENCE_REL = "SessionOutput/Audio_Reference.wav"
SESSION_TTS_DIR_REL = "SessionOutput/tts"
SESSION_TTS_FINAL_REL = f"{SESSION_TTS_DIR_REL}/tts_builder_candidates.json"
DEFAULT_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_CATALOG_REL = f"OpenCrew/ToolLibrary/Analysis_V1/VoiceCatalog/{DEFAULT_TTS_MODEL}"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_HTTP_RETRY_DELAYS = (1.0, 2.0, 4.0)
CATALOG_SAMPLE_TEXT_ID = "fixed_cn_v1"
OPENCREW_DATA_DIR_ENV = "OPENCREW_DATA_DIR"
SPEECHBRAIN_CACHE_DIR_ENV = "ANALYSIS_V1_SPEECHBRAIN_CACHE_DIR"
SPEECHBRAIN_DOWNLOAD_ENV = "ANALYSIS_V1_ALLOW_SPEECHBRAIN_DOWNLOAD"
SPEECHBRAIN_ENABLE_ENV = "ANALYSIS_V1_ENABLE_SPEECHBRAIN"
GEMINI_VOICE_GENDER_HINTS = {
    "Aoede": "female",
    "Autonoe": "female",
    "Callirrhoe": "female",
    "Despina": "female",
    "Erinome": "female",
    "Kore": "female",
    "Laomedeia": "female",
    "Pulcherrima": "female",
    "Sadachbia": "female",
    "Sulafat": "female",
    "Vindemiatrix": "female",
    "Zephyr": "female",
    "Achird": "male",
    "Algenib": "male",
    "Algieba": "male",
    "Alnilam": "male",
    "Charon": "male",
    "Enceladus": "male",
    "Fenrir": "male",
    "Gacrux": "male",
    "Iapetus": "male",
    "Leda": "male",
    "Orus": "male",
    "Puck": "male",
    "Rasalgethi": "male",
    "Sadaltager": "male",
    "Schedar": "male",
    "Umbriel": "male",
    "Zubenelgenubi": "male",
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

warnings.filterwarnings("ignore", message=r".*torchaudio\._backend\.list_audio_backends.*")
warnings.filterwarnings("ignore", message=r".*Module 'speechbrain\.pretrained' was deprecated.*")
warnings.filterwarnings("ignore", message=r".*urllib3 v2 only supports OpenSSL.*")


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(RuntimeError):
    pass


class CandidatePoolExhaustedError(ToolError):
    def __init__(self, *, generated: int, required: int, attempted: int, failures: int):
        super().__init__(
            f"Generated only {generated} of {required} TTS candidates after trying "
            f"{attempted} ranked voices ({failures} candidate-local failures)."
        )
        self.generated = generated
        self.required = required
        self.attempted = attempted
        self.failures = failures


@dataclass(frozen=True)
class Args:
    workspace: str
    voice_catalog_dir: str
    provider: str
    model: str
    voices: str
    reference_start: float
    reference_duration: float
    final_count: int
    database_url: str
    database_url_env: str
    force: bool
    resume: bool
    print_json: bool


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
        out = run_cmd([
            find_binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ], timeout=30)
        return float(out.strip())
    except Exception:
        return wav_duration(path)


def wav_duration(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        with wave.open(str(path), "rb") as reader:
            return reader.getnframes() / float(reader.getframerate())
    except Exception:
        return 0.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_catalog_dir(workspace: Path, args: Args) -> Path:
    raw = str(args.voice_catalog_dir or "").strip()
    if not raw:
        raise BlockedError("voice_catalog_dir_missing", "--voice-catalog-dir is required for 03_02_TTSBuilderQuick.")
    path = Path(raw).expanduser()
    return path if path.is_absolute() else workspace / path


def ensure_dirs(workspace: Path) -> None:
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        PROMPT_DIR_REL,
        WORKING_RAW_DIR_REL,
        WORKING_FIT_DIR_REL,
        f"{TOOL_DIR_NAME}/Report",
        SESSION_TTS_DIR_REL,
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "runtime": {
            "python_executable": sys.executable,
            "python_version": sys.version.split()[0],
        },
        "requires_database": True,
        "requires_model_calls": True,
        "model_call_policy": {
            "tts_model_calls": "bounded_ranked_pool_until_final_count_after_local_catalog_match",
            "max_ranked_pool_multiplier": 2,
            "catalog_missing_policy": "blocked_required_system_audio",
        },
        "error": None,
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
    result["error"] = {"code": code, "message": message}


def looks_like_resource_exhausted(message: str) -> bool:
    lower = str(message or "").lower()
    return "resource_exhausted" in lower or "prepayment credit" in lower or "credits are depleted" in lower


def classify_failure(exc: Exception) -> tuple[str, str]:
    message = str(exc)
    lower = message.lower()
    if isinstance(exc, CandidatePoolExhaustedError):
        return "tts_candidate_pool_exhausted", message
    if isinstance(exc, ToolError):
        if "http 429" in lower and looks_like_resource_exhausted(message):
            return "provider_resource_exhausted", message
        if lower.startswith("http "):
            return "provider_request_failed", message
        if lower.startswith("network error"):
            return "provider_network_error", message
    return "unexpected_error", message


def add_failure(result: dict[str, Any], exc: Exception) -> None:
    code, message = classify_failure(exc)
    result["status"] = "failed"
    result["error"] = {"code": code, "message": message}
    result["warnings"].append({"code": code, "message": message})


def scan_for_sensitive_output(payload: dict[str, Any]) -> list[dict[str, str]]:
    text = json.dumps(payload, ensure_ascii=False).lower()
    return [{"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"} for pattern in SECRET_PATTERNS if pattern in text]


def validate_workspace(workspace: Path) -> None:
    if not workspace.exists() or not workspace.is_dir():
        raise BlockedError("workspace_missing", f"Analysis_V1 workspace does not exist: {workspace}")
    for rel in (VARIABLES_REL, SESSION_FINAL_ITEMS_REL, SESSION_AUDIO_REFERENCE_REL):
        path = workspace / rel
        if not path.exists():
            raise BlockedError("required_input_missing", f"Required input is missing: {rel}")


def load_variables(workspace: Path) -> dict[str, Any]:
    payload = read_json(workspace / VARIABLES_REL)
    return payload if isinstance(payload, dict) else {}


def load_final_items(workspace: Path) -> dict[str, Any]:
    payload = read_json(workspace / SESSION_FINAL_ITEMS_REL)
    if not isinstance(payload, dict):
        raise BlockedError("final_srt_frame_items_invalid", f"{SESSION_FINAL_ITEMS_REL} must be a JSON object.")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise BlockedError("final_srt_frame_items_empty", f"{SESSION_FINAL_ITEMS_REL} has no items.")
    return payload


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
        "message": "Previous SessionOutput TTS candidates were restored because the forced Builder-Quick rerun did not complete.",
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


def selected_dialogue(items: list[dict[str, Any]]) -> str:
    return "\n".join(dialogue(item) for item in items if dialogue(item)).strip()


def count_cjk(text: str) -> int:
    return sum(1 for ch in str(text or "") if "\u4e00" <= ch <= "\u9fff")


def resolve_reference_audio(workspace: Path, args: Args, target_duration: float) -> Path:
    path = workspace / SESSION_AUDIO_REFERENCE_REL
    if not path.exists() or not path.is_file():
        raise BlockedError("reference_audio_missing", f"Required reference audio is missing: {SESSION_AUDIO_REFERENCE_REL}. Run 02_01_AudioASR.py first.")
    reference_start = max(0.0, float(args.reference_start or 0.0))
    reference_duration = float(args.reference_duration or 0.0)
    if reference_start <= 0 and reference_duration <= 0:
        return path
    selected = workspace / WORKING_REFERENCE_REL
    selected.parent.mkdir(parents=True, exist_ok=True)
    duration = reference_duration if reference_duration > 0 else target_duration
    run_cmd([
        find_binary("ffmpeg"),
        "-y",
        "-ss",
        f"{reference_start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(selected),
    ], timeout=120)
    if selected.exists() and selected.stat().st_size > 0:
        return selected
    return path


def extract_reference_audio(workspace: Path, start: float, duration: float) -> Path:
    source = workspace / SESSION_AUDIO_REFERENCE_REL
    if not source.exists() or not source.is_file():
        raise BlockedError("reference_audio_missing", f"Required reference audio is missing: {SESSION_AUDIO_REFERENCE_REL}. Run 02_01_AudioASR.py first.")
    selected = workspace / WORKING_REFERENCE_REL
    selected.parent.mkdir(parents=True, exist_ok=True)
    safe_start = max(0.0, float(start or 0.0))
    safe_duration = max(0.1, float(duration or 16.0))
    run_cmd([
        find_binary("ffmpeg"),
        "-y",
        "-ss",
        f"{safe_start:.3f}",
        "-t",
        f"{safe_duration:.3f}",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(selected),
    ], timeout=120)
    if not selected.exists() or selected.stat().st_size <= 0:
        raise BlockedError("reference_audio_extract_failed", f"Could not extract selected reference audio range {safe_start:.3f}-{safe_start + safe_duration:.3f}.")
    return selected


def read_wav_samples(path: Path) -> tuple[list[float], int, float]:
    try:
        with wave.open(str(path), "rb") as reader:
            frames = reader.readframes(reader.getnframes())
            width = reader.getsampwidth()
            channels = reader.getnchannels()
            rate = reader.getframerate()
            duration = reader.getnframes() / float(rate) if rate else 0.0
            if width != 2:
                return [], rate, duration
            values = struct.unpack("<" + "h" * (len(frames) // 2), frames)
            if channels > 1:
                values = values[::channels]
            return [value / 32768.0 for value in values], rate, duration
    except Exception:
        return [], 0, 0.0


def weighted_median(values: list[float], weights: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(zip(values, weights), key=lambda item: item[0])
    total = sum(max(0.0, float(weight)) for _, weight in ordered)
    if total <= 0:
        return float(ordered[len(ordered) // 2][0])
    midpoint = total / 2.0
    running = 0.0
    for value, weight in ordered:
        running += max(0.0, float(weight))
        if running >= midpoint:
            return float(value)
    return float(ordered[-1][0])


def estimate_autocorr_pitch(samples: list[float], rate: int) -> tuple[float, float]:
    if np is None or not samples or rate <= 0:
        return 0.0, 0.0
    data = np.asarray(samples, dtype=np.float32)
    if data.size < int(rate * 0.08):
        return 0.0, 0.0
    max_samples = min(data.size, int(rate * 16))
    data = data[:max_samples]
    frame_size = max(256, int(rate * 0.04))
    hop = max(128, int(rate * 0.01))
    if data.size < frame_size:
        data = np.pad(data, (0, frame_size - data.size), mode="constant")
    min_lag = max(1, int(rate / 500.0))
    max_lag = min(frame_size - 2, int(rate / 60.0))
    if max_lag <= min_lag:
        return 0.0, 0.0
    rms_values: list[float] = []
    frames: list[Any] = []
    for start in range(0, max(1, data.size - frame_size + 1), hop):
        frame = data[start:start + frame_size]
        if frame.size < frame_size:
            continue
        rms = float(np.sqrt(np.mean(frame * frame)))
        rms_values.append(rms)
        frames.append(frame)
    if not frames:
        return 0.0, 0.0
    energy_floor = max(0.004, float(np.percentile(np.asarray(rms_values), 65)) * 0.55)
    pitches: list[float] = []
    weights: list[float] = []
    for frame, rms in zip(frames, rms_values):
        if rms < energy_floor:
            continue
        centered = frame - np.mean(frame)
        if float(np.max(np.abs(centered))) < 1e-4:
            continue
        windowed = centered * np.hanning(centered.size)
        corr = np.correlate(windowed, windowed, mode="full")[windowed.size - 1:]
        base = float(corr[0]) if corr.size else 0.0
        if base <= 1e-9:
            continue
        search = corr[min_lag:max_lag + 1]
        if search.size <= 0:
            continue
        lag = int(np.argmax(search)) + min_lag
        peak = float(corr[lag] / base)
        if peak < 0.22:
            continue
        pitch = rate / float(lag)
        if 60.0 <= pitch <= 500.0:
            pitches.append(float(pitch))
            weights.append(float(peak * max(0.001, rms)))
    if not pitches:
        return 0.0, 0.0
    confidence = min(1.0, (len(pitches) / max(1, len(frames))) * 1.8)
    return weighted_median(pitches, weights), float(confidence)


def estimate_centroid_and_pitch(samples: list[float], rate: int) -> tuple[float, float, float, str, float]:
    if np is None or not samples or rate <= 0:
        return 0.0, 0.0, 0.0, "unavailable", 0.0
    data = np.asarray(samples, dtype=np.float32)
    if data.size < 256:
        return 0.0, 0.0, 0.0, "too_short", 0.0
    max_samples = min(data.size, int(rate * 8))
    data = data[:max_samples]
    data = data - np.mean(data)
    window = np.hanning(data.size)
    spectrum = np.abs(np.fft.rfft(data * window))
    freqs = np.fft.rfftfreq(data.size, d=1.0 / rate)
    total = float(np.sum(spectrum))
    centroid = float(np.sum(freqs * spectrum) / total) if total > 1e-9 else 0.0
    mask = (freqs >= 60.0) & (freqs <= 420.0)
    if np.any(mask):
        local = spectrum[mask]
        local_freqs = freqs[mask]
        spectral_pitch = float(local_freqs[int(np.argmax(local))])
    else:
        spectral_pitch = 0.0
    autocorr_pitch, autocorr_confidence = estimate_autocorr_pitch(samples, rate)
    if autocorr_pitch > 0 and autocorr_confidence >= 0.18:
        return centroid, autocorr_pitch, autocorr_confidence, "autocorrelation_median_f0", spectral_pitch
    return centroid, spectral_pitch, 0.0, "spectral_peak_fallback", spectral_pitch


def audio_features(path: Path, speaking_rate_cps: float = 0.0) -> dict[str, Any]:
    samples, rate, duration = read_wav_samples(path)
    if not samples:
        return {
            "duration": wav_duration(path),
            "sample_rate": float(rate),
            "rms": 0.0,
            "zero_crossing": 0.0,
            "energy": 0.0,
            "spectral_centroid": 0.0,
            "pitch_hz": 0.0,
            "pitch_confidence": 0.0,
            "pitch_method": "unavailable",
            "spectral_peak_hz": 0.0,
            "speaking_rate_cps": float(speaking_rate_cps or 0.0),
            "signal_quality": 0.0,
        }
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    crossings = sum(1 for left, right in zip(samples, samples[1:]) if (left < 0 <= right) or (left >= 0 > right))
    zcr = crossings / max(1, len(samples) - 1)
    centroid, pitch, pitch_confidence, pitch_method, spectral_pitch = estimate_centroid_and_pitch(samples, rate)
    energy = min(1.0, rms / 0.18)
    signal_quality = max(0.0, min(1.0, energy + 0.20))
    return {
        "duration": float(duration),
        "sample_rate": float(rate),
        "rms": float(rms),
        "zero_crossing": float(zcr),
        "energy": float(energy),
        "spectral_centroid": float(centroid),
        "pitch_hz": float(pitch),
        "pitch_confidence": float(pitch_confidence),
        "pitch_method": pitch_method,
        "spectral_peak_hz": float(spectral_pitch),
        "speaking_rate_cps": float(speaking_rate_cps or 0.0),
        "signal_quality": float(signal_quality),
    }


def rounded_feature_map(features: dict[str, Any]) -> dict[str, Any]:
    rounded: dict[str, Any] = {}
    for key, value in features.items():
        if isinstance(value, (int, float)):
            rounded[key] = round(float(value), 6)
        else:
            rounded[key] = value
    return rounded


def default_speechbrain_cache_root() -> Path:
    raw = os.environ.get(SPEECHBRAIN_CACHE_DIR_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    data_dir = Path(os.environ.get(OPENCREW_DATA_DIR_ENV) or (Path.home() / ".opencrew")).expanduser()
    return data_dir / "model_cache" / "analysis_v1" / "speechbrain"


def speechbrain_cache_has_files(savedir: Path) -> bool:
    try:
        return savedir.exists() and any(savedir.iterdir())
    except Exception:
        return False


def local_import_context() -> None:
    os.environ.setdefault("NUMBA_CACHE_DIR", "/private/tmp/opencrew_numba_cache")
    os.environ.setdefault("LIBROSA_CACHE_DIR", "/private/tmp/opencrew_librosa_cache")
    # Some user-site librosa/numba installs cannot create a cache locator on import.
    # Disabling JIT keeps Resemblyzer usable for small catalog batches.
    os.environ.setdefault("NUMBA_DISABLE_JIT", "1")
    for path in (str(RESEMBLYZER_ROOT), str(SPEECHBRAIN_ROOT)):
        if path not in sys.path and Path(path).exists():
            sys.path.insert(0, path)


def load_resemblyzer_backend(result: dict[str, Any]) -> Any | None:
    if np is None:
        result.setdefault("warnings", []).append({"code": "resemblyzer_unavailable", "message": "NumPy is unavailable; Resemblyzer scoring is disabled."})
        return None
    try:
        local_import_context()
        from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
        from resemblyzer.hparams import sampling_rate  # type: ignore

        patch_resemblyzer_mel()
        return {
            "encoder": VoiceEncoder(verbose=False),
            "preprocess_wav": preprocess_wav,
            "sampling_rate": int(sampling_rate),
            "source": str(RESEMBLYZER_ROOT),
        }
    except Exception as exc:
        result.setdefault("warnings", []).append({"code": "resemblyzer_unavailable", "message": f"Resemblyzer scoring is disabled: {exc}"})
        return None


def load_audio_array(path: Path, target_rate: int) -> tuple[Any | None, int]:
    if np is None:
        return None, 0
    try:
        from scipy.io import wavfile  # type: ignore
        from scipy.signal import resample_poly  # type: ignore

        rate, data = wavfile.read(str(path))
        array = np.asarray(data)
        if array.ndim > 1:
            array = np.mean(array, axis=1)
        if np.issubdtype(array.dtype, np.integer):
            info = np.iinfo(array.dtype)
            if info.min == 0:
                array = (array.astype(np.float32) - (info.max / 2.0)) / max(1.0, info.max / 2.0)
            else:
                array = array.astype(np.float32) / max(abs(info.min), info.max)
        else:
            array = array.astype(np.float32)
        array = np.nan_to_num(array, nan=0.0, posinf=0.0, neginf=0.0)
        array = np.clip(array, -1.0, 1.0)
        if target_rate > 0 and rate > 0 and int(rate) != int(target_rate):
            divisor = math.gcd(int(rate), int(target_rate))
            array = resample_poly(array, int(target_rate) // divisor, int(rate) // divisor).astype(np.float32)
            rate = target_rate
        return array.astype(np.float32), int(rate)
    except Exception:
        return None, 0


def hz_to_mel(freq: float) -> float:
    return 2595.0 * math.log10(1.0 + freq / 700.0)


def mel_to_hz(mel: float) -> float:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def mel_filterbank(sample_rate: int, n_fft: int, n_mels: int) -> Any:
    if np is None:
        return None
    low_mel = hz_to_mel(0.0)
    high_mel = hz_to_mel(sample_rate / 2.0)
    mel_points = np.linspace(low_mel, high_mel, n_mels + 2)
    hz_points = np.asarray([mel_to_hz(float(value)) for value in mel_points])
    bins = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)
    filters = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for idx in range(1, n_mels + 1):
        left = int(bins[idx - 1])
        center = int(bins[idx])
        right = int(bins[idx + 1])
        if center > left:
            filters[idx - 1, left:center] = (np.arange(left, center) - left) / max(1, center - left)
        if right > center:
            filters[idx - 1, center:right] = (right - np.arange(center, right)) / max(1, right - center)
    return filters


def resemblyzer_mel_spectrogram(wav: Any, sample_rate: int, window_ms: float, step_ms: float, n_mels: int) -> Any:
    if np is None:
        return None
    audio = np.asarray(wav, dtype=np.float32)
    n_fft = max(1, int(sample_rate * window_ms / 1000.0))
    hop = max(1, int(sample_rate * step_ms / 1000.0))
    if audio.size < n_fft:
        audio = np.pad(audio, (0, n_fft - audio.size), mode="constant")
    audio = np.pad(audio, (n_fft // 2, n_fft // 2), mode="constant")
    starts = range(0, max(1, audio.size - n_fft + 1), hop)
    window = np.hanning(n_fft).astype(np.float32)
    frames = []
    filters = mel_filterbank(sample_rate, n_fft, n_mels)
    for start in starts:
        frame = audio[start:start + n_fft]
        if frame.size < n_fft:
            frame = np.pad(frame, (0, n_fft - frame.size), mode="constant")
        spectrum = np.abs(np.fft.rfft(frame * window, n=n_fft)) ** 2
        frames.append(np.dot(filters, spectrum).astype(np.float32))
    if not frames:
        return np.zeros((1, n_mels), dtype=np.float32)
    return np.asarray(frames, dtype=np.float32)


def patch_resemblyzer_mel() -> None:
    import resemblyzer.audio as audio_module  # type: ignore
    from resemblyzer.hparams import mel_n_channels, mel_window_length, mel_window_step, sampling_rate  # type: ignore

    def _wav_to_mel_spectrogram(wav: Any) -> Any:
        return resemblyzer_mel_spectrogram(wav, int(sampling_rate), float(mel_window_length), float(mel_window_step), int(mel_n_channels))

    audio_module.wav_to_mel_spectrogram = _wav_to_mel_spectrogram


def resemblyzer_embedding(path: Path, backend: Any | None) -> Any | None:
    if backend is None or np is None:
        return None
    try:
        wav_array, source_sr = load_audio_array(path, int(backend.get("sampling_rate") or 16000))
        if wav_array is None:
            return None
        wav = backend["preprocess_wav"](wav_array, source_sr=None if source_sr == int(backend.get("sampling_rate") or 16000) else source_sr)
        embed = backend["encoder"].embed_utterance(wav)
        return embed / max(1e-9, float(np.linalg.norm(embed)))
    except Exception:
        return None


def load_speechbrain_backend(result: dict[str, Any], cache_root: Path | None = None) -> Any | None:
    allow_network = os.environ.get(SPEECHBRAIN_DOWNLOAD_ENV) == "1"
    enabled = os.environ.get(SPEECHBRAIN_ENABLE_ENV) == "1" or allow_network
    if not enabled:
        result.setdefault("warnings", []).append({
            "code": "speechbrain_disabled",
            "message": f"SpeechBrain ECAPA scoring is optional and disabled for UI stability. Set {SPEECHBRAIN_ENABLE_ENV}=1 to enable it.",
        })
        return None
    try:
        local_import_context()
        from speechbrain.inference.speaker import SpeakerRecognition  # type: ignore
        from speechbrain.utils.fetching import FetchConfig  # type: ignore

        resolved_cache_root = cache_root or default_speechbrain_cache_root()
        savedir = resolved_cache_root / "speechbrain_spkrec_ecapa_voxceleb"
        savedir.mkdir(parents=True, exist_ok=True)
        if not allow_network and not speechbrain_cache_has_files(savedir):
            result.setdefault("warnings", []).append({
                "code": "speechbrain_model_cache_empty",
                "message": (
                    "SpeechBrain ECAPA scoring is disabled because the persistent model cache is empty and downloads are disabled. "
                    f"Cache directory: {savedir}. Set {SPEECHBRAIN_DOWNLOAD_ENV}=1 once to populate it."
                ),
            })
            return None
        verifier = SpeakerRecognition.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(savedir),
            run_opts={"device": "cpu"},
            fetch_config=FetchConfig(allow_network=allow_network),
        )
        return {
            "verifier": verifier,
            "source": "speechbrain/spkrec-ecapa-voxceleb",
            "savedir": str(savedir),
            "allow_network": allow_network,
        }
    except Exception as exc:
        result.setdefault("warnings", []).append({
            "code": "speechbrain_unavailable",
            "message": f"SpeechBrain ECAPA scoring is disabled under {sys.executable}: {exc}",
        })
        return None


def speechbrain_embedding(path: Path, backend: Any | None) -> Any | None:
    if backend is None:
        return None
    waveform = backend["verifier"].load_audio(str(path))
    batch = waveform.unsqueeze(0)
    with __import__("torch").no_grad():
        embed = backend["verifier"].encode_batch(batch, normalize=False)
    return embed.squeeze().detach().cpu()


def wav_from_pcm(raw: bytes, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2) -> bytes:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        writer.writeframes(raw)
    return buffer.getvalue()


def post_json(
    url: str,
    payload: dict[str, Any],
    timeout: int = 180,
    use_environment_proxy: bool = True,
    retry_delays: tuple[float, ...] = DEFAULT_HTTP_RETRY_DELAYS,
) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if not use_environment_proxy else None
    delays = tuple(retry_delays or ())
    for attempt_index in range(len(delays) + 1):
        try:
            with (opener.open(request, timeout=timeout) if opener else urllib.request.urlopen(request, timeout=timeout)) as response:
                body = response.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:3000]
            non_retryable_quota = int(exc.code) == 429 and looks_like_resource_exhausted(detail)
            if int(exc.code) in TRANSIENT_HTTP_STATUS_CODES and not non_retryable_quota and attempt_index < len(delays):
                time.sleep(delays[attempt_index])
                continue
            attempts = attempt_index + 1
            suffix = f" after {attempts} attempts" if attempts > 1 else ""
            raise ToolError(f"HTTP {exc.code}{suffix}: {detail}") from exc
        except urllib.error.URLError as exc:
            if attempt_index < len(delays):
                time.sleep(delays[attempt_index])
                continue
            attempts = attempt_index + 1
            suffix = f" after {attempts} attempts" if attempts > 1 else ""
            raise ToolError(f"Network error{suffix}: {exc}") from exc
    raise ToolError("HTTP request failed without a response.")


def gemini_tts_payload(prompt_text: str, voice: str) -> dict[str, Any]:
    return {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }


def extract_inline_audio(response: dict[str, Any], output_path: Path) -> dict[str, Any] | None:
    for candidate in response.get("candidates") or []:
        content = candidate.get("content") if isinstance(candidate, dict) else {}
        if not isinstance(content, dict):
            continue
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
    return "http 400" in message and ("invalid_argument" in message or "invalid argument" in message)


def is_global_tts_provider_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if "network error" in message or looks_like_resource_exhausted(message):
        return True
    return any(f"http {status}" in message for status in (401, 403, 408, 429, 500, 502, 503, 504))


def is_candidate_local_tts_error(exc: Exception) -> bool:
    if not isinstance(exc, ToolError) or is_global_tts_provider_error(exc):
        return False
    message = str(exc).lower()
    return (
        is_gemini_invalid_argument_error(exc)
        or "response_without_audio" in message
        or "did not return audio" in message
    )


def record_tts_audit(
    *,
    workspace: Path | None,
    asset_key: str,
    model: str,
    voice: str,
    prompt_path: Path,
    response: dict[str, Any] | None,
    status: str,
    error_code: str = "",
) -> None:
    if workspace is None:
        return
    try:
        record_model_call_audit(
            workspace=workspace,
            tool_dir_name=TOOL_DIR_NAME,
            tool_name=TOOL_NAME,
            step_index=5,
            asset_key=asset_key,
            kind="TTS",
            provider="google",
            model_id=model,
            request={"provider": "google", "model": model, "voice": voice, "prompt_path": prompt_path.relative_to(workspace).as_posix() if prompt_path.is_relative_to(workspace) else str(prompt_path)},
            response=response,
            status=status,
            error_code=error_code,
            prompt_path=prompt_path.relative_to(workspace).as_posix() if prompt_path.is_relative_to(workspace) else str(prompt_path),
            output_summary=gemini_tts_finish_summary(response or {}) or status,
        )
    except Exception:
        return


def call_gemini_tts(api_key: str, model: str, voice: str, prompt_path: Path, output_path: Path, *, workspace: Path | None = None, asset_key: str = "") -> dict[str, Any]:
    prompt_text = prompt_path.read_text(encoding="utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
    try:
        response = post_json(url, gemini_tts_payload(prompt_text, voice), timeout=180, use_environment_proxy=False)
    except ToolError as exc:
        if not is_gemini_invalid_argument_error(exc):
            record_tts_audit(
                workspace=workspace,
                asset_key=f"{asset_key or output_path.stem}_primary",
                model=model,
                voice=voice,
                prompt_path=prompt_path,
                response={"error": str(exc)[:1000]},
                status="error",
                error_code="primary_request_failed",
            )
            raise
        record_tts_audit(
            workspace=workspace,
            asset_key=f"{asset_key or output_path.stem}_primary",
            model=model,
            voice=voice,
            prompt_path=prompt_path,
            response={"error": str(exc)[:1000], "retry_policy": "body_only_after_invalid_argument"},
            status="error",
            error_code="primary_invalid_argument",
        )
        retry_path = write_gemini_tts_body_only_retry_prompt(prompt_path, prompt_text)
        try:
            retry_response = post_json(url, gemini_tts_payload(retry_path.read_text(encoding="utf-8"), voice), timeout=180, use_environment_proxy=False)
        except ToolError as retry_exc:
            record_tts_audit(
                workspace=workspace,
                asset_key=f"{asset_key or output_path.stem}_retry_body_only",
                model=model,
                voice=voice,
                prompt_path=retry_path,
                response={"error": str(retry_exc)[:1000], "retry_reason": "primary_invalid_argument"},
                status="error",
                error_code="retry_request_failed",
            )
            raise
        retry_audio_meta = extract_inline_audio(retry_response, output_path)
        if retry_audio_meta is not None:
            record_tts_audit(workspace=workspace, asset_key=f"{asset_key or output_path.stem}_retry_body_only", model=model, voice=voice, prompt_path=retry_path, response={**retry_response, **retry_audio_meta}, status="ok")
            return {
                **retry_audio_meta,
                "prompt_path": str(retry_path),
                "retry_used": True,
                "retry_reason": "primary_invalid_argument",
            }
        record_tts_audit(workspace=workspace, asset_key=f"{asset_key or output_path.stem}_retry_body_only", model=model, voice=voice, prompt_path=retry_path, response=retry_response, status="error", error_code="retry_response_without_audio")
        raise ToolError(
            "Gemini TTS invalid argument and body-only retry did not return audio: "
            f"primary={str(exc)[:500]} retry={json.dumps(retry_response, ensure_ascii=False)[:900]}"
        ) from exc
    audio_meta = extract_inline_audio(response, output_path)
    if audio_meta is not None:
        record_tts_audit(workspace=workspace, asset_key=asset_key or output_path.stem, model=model, voice=voice, prompt_path=prompt_path, response={**response, **audio_meta}, status="ok")
        return {**audio_meta, "prompt_path": str(prompt_path), "retry_used": False}
    record_tts_audit(workspace=workspace, asset_key=f"{asset_key or output_path.stem}_primary", model=model, voice=voice, prompt_path=prompt_path, response=response, status="error", error_code="primary_response_without_audio")
    retry_path = write_gemini_tts_retry_prompt(prompt_path, prompt_text)
    try:
        retry_response = post_json(url, gemini_tts_payload(retry_path.read_text(encoding="utf-8"), voice), timeout=180, use_environment_proxy=False)
    except ToolError as retry_exc:
        record_tts_audit(
            workspace=workspace,
            asset_key=f"{asset_key or output_path.stem}_retry",
            model=model,
            voice=voice,
            prompt_path=retry_path,
            response={
                "error": str(retry_exc)[:1000],
                "retry_reason": gemini_tts_finish_summary(response) or "primary_response_without_audio",
            },
            status="error",
            error_code="retry_request_failed",
        )
        raise
    retry_audio_meta = extract_inline_audio(retry_response, output_path)
    if retry_audio_meta is not None:
        record_tts_audit(workspace=workspace, asset_key=f"{asset_key or output_path.stem}_retry", model=model, voice=voice, prompt_path=retry_path, response={**retry_response, **retry_audio_meta}, status="ok")
        return {
            **retry_audio_meta,
            "prompt_path": str(retry_path),
            "retry_used": True,
            "retry_reason": gemini_tts_finish_summary(response) or "primary_response_without_audio",
        }
    record_tts_audit(workspace=workspace, asset_key=f"{asset_key or output_path.stem}_retry", model=model, voice=voice, prompt_path=retry_path, response=retry_response, status="error", error_code="retry_response_without_audio")
    raise ToolError(
        "Gemini TTS did not return audio after retry: "
        f"primary={json.dumps(response, ensure_ascii=False)[:900]} "
        f"retry={json.dumps(retry_response, ensure_ascii=False)[:900]}"
    )


def load_tts_builder_g_module() -> Any | None:
    module_path = Path(__file__).with_name("03_01_TTSBuilderG.py")
    module_name = "analysis_v1_tts_builder_g_helpers"
    if module_name in sys.modules:
        return sys.modules[module_name]
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def legacy_03_01_args(helper: Any, args: Args, model: str) -> Any:
    return helper.Args(
        workspace=args.workspace,
        mode="normal",
        scene_profile_mode="auto",
        tts_model=model,
        scene_model="",
        voices="",
        target_duration=16.0,
        quick_duration=8.0,
        reference_start=args.reference_start,
        reference_duration=args.reference_duration,
        top_voices=3,
        final_count=args.final_count,
        max_scene_frames=8,
        database_url=args.database_url,
        database_url_env=args.database_url_env,
        force=args.force,
        resume=args.resume,
        force_regenerate_prompts=False,
        print_json=args.print_json,
    )


def load_tts_api_key(args: Args, provider: str, model: str) -> str:
    env_key = os.environ.get("OPENCREW_TTS_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    env_provider = os.environ.get("OPENCREW_TTS_PROVIDER", "").strip()
    if env_key and env_provider in {"", "google", "gemini"}:
        return env_key
    helper = load_tts_builder_g_module()
    if helper is None:
        raise BlockedError("gemini_api_key_loader_unavailable", "Cannot load 03_01_TTSBuilderG API-key helper.")
    return str(helper.load_google_api_key(legacy_03_01_args(helper, args, model), kind="tts", provider=provider, model=model)).strip()


def load_scene_profile(workspace: Path, variables: dict[str, Any], reference_text: str) -> dict[str, Any]:
    for rel in (
        "S5_03_01_TTSBuilderG/Output/tts_builder_candidates.json",
        "SessionOutput/tts/tts_builder_candidates.json",
    ):
        path = workspace / rel
        if not path.exists():
            continue
        try:
            payload = read_json(path)
            scene_profile = payload.get("scene_profile")
            if isinstance(scene_profile, dict) and scene_profile:
                return {**scene_profile, "source": scene_profile.get("source") or rel}
        except Exception:
            continue
    return {
        "scene_type": "short_video_product_recommendation",
        "speaker_profile": "中文短视频口播者",
        "environment": "",
        "emotion": "自然、轻松、克制",
        "delivery_style": "自然近距离口播",
        "pace": "自然偏快但不赶",
        "avoid": ["不要播音腔", "不要广告腔", "不要直播叫卖感", "不要夸张重音", "不要尾音上扬太多"],
        "voice_prompt_guidance": {
            "speaker": "普通中文短视频口播者",
            "scene": "自然生活分享",
            "delivery": "像日常自拍视频里自然说话，清楚但不表演",
            "emotion": "自然、轻松、克制",
            "pace": "自然偏快但不赶",
            "recording_style": "近距离、真实、干净",
            "naturalness": "high",
            "performance_risk": "避免广告腔、叫卖感、夸张重音和过度兴奋",
        },
        "dialogue_evidence": [reference_text[:160]],
        "source": "03_02_rule_fallback",
    }


def normalize_gender(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"female", "woman", "girl", "女", "女声", "女性"}:
        return "female"
    if text in {"male", "man", "boy", "男", "男声", "男性"}:
        return "male"
    if "female" in text or "woman" in text or "girl" in text or "女" in text:
        return "female"
    if "male" in text or "man" in text or "boy" in text or "男" in text:
        return "male"
    return ""


def infer_target_gender(scene_profile: dict[str, Any], reference_features: dict[str, Any]) -> dict[str, Any]:
    guidance = scene_profile.get("voice_prompt_guidance") if isinstance(scene_profile.get("voice_prompt_guidance"), dict) else {}
    evidence = " ".join(
        str(value or "")
        for value in (
            scene_profile.get("speaker_gender"),
            scene_profile.get("gender"),
            scene_profile.get("speaker_profile"),
            guidance.get("speaker") if isinstance(guidance, dict) else "",
        )
    )
    profile_gender = normalize_gender(evidence)
    pitch = float(reference_features.get("pitch_hz") or 0.0)
    pitch_confidence = float(reference_features.get("pitch_confidence") or 0.0)
    spectral_peak = float(reference_features.get("spectral_peak_hz") or 0.0)
    pitch_gender = ""
    pitch_source = "unknown"
    if pitch_confidence >= 0.18:
        if 80 <= pitch <= 175:
            pitch_gender = "male"
            pitch_source = "reference_pitch"
        elif pitch >= 230:
            pitch_gender = "female"
            pitch_source = "reference_pitch"
        elif 175 < pitch < 230:
            harmonic_ratio = pitch / spectral_peak if spectral_peak > 0 else 0.0
            if 80 <= spectral_peak <= 175 and 1.75 <= harmonic_ratio <= 2.25:
                pitch_gender = "male"
                pitch_source = "reference_pitch_spectral_harmonic"
            elif spectral_peak >= 230:
                pitch_gender = "female"
                pitch_source = "reference_pitch_spectral_peak"
            else:
                pitch_source = "reference_pitch_ambiguous"
    elif 80 <= spectral_peak <= 175:
        pitch_gender = "male"
        pitch_source = "reference_spectral_peak"
    elif spectral_peak >= 230:
        pitch_gender = "female"
        pitch_source = "reference_spectral_peak"
    gender = profile_gender or pitch_gender
    confidence = "high" if profile_gender else "medium" if pitch_gender and pitch_confidence >= 0.35 else "low" if not pitch_gender else "medium_low"
    return {
        "target_gender": gender,
        "confidence": confidence,
        "source": "scene_profile" if profile_gender else pitch_source,
        "reference_pitch_hz": round(pitch, 3),
        "reference_pitch_confidence": round(pitch_confidence, 3),
        "reference_pitch_method": reference_features.get("pitch_method") or "",
        "reference_spectral_peak_hz": round(spectral_peak, 3),
    }


def catalog_voice_gender(item: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    voice = str(item.get("voice") or item.get("voice_id") or "").strip()
    catalog_gender = normalize_gender(item.get("gender"))
    if catalog_gender:
        return {"gender": catalog_gender, "source": "catalog_index", "voice": voice}
    hinted = GEMINI_VOICE_GENDER_HINTS.get(voice, "")
    if hinted:
        return {"gender": hinted, "source": "curated_gemini_voice_hint", "voice": voice}
    pitch = float(features.get("pitch_hz") or 0.0)
    inferred = "female" if pitch >= 230 else "male" if 80 <= pitch <= 175 else ""
    return {"gender": inferred, "source": "pitch_fallback" if inferred else "unknown", "voice": voice}


def gender_match(target: dict[str, Any], candidate: dict[str, Any]) -> bool:
    target_gender = str(target.get("target_gender") or "")
    candidate_gender = str(candidate.get("gender") or "")
    if not target_gender:
        return True
    return target_gender == candidate_gender


def build_model_prompt(scene_profile: dict[str, Any], voice: str, sample_text: str, tempo_prior: float, variant: str) -> str:
    helper = load_tts_builder_g_module()
    tempo_hint = ""
    if tempo_prior > 1.08:
        tempo_hint = "本地 catalog 先验显示该 voice 相对偏快，请语速略慢一点，保留自然停顿，但不要拖腔。"
    elif 0 < tempo_prior < 0.92:
        tempo_hint = "本地 catalog 先验显示该 voice 相对偏慢，请语速略快一点，停顿更短，但不要急促。"
    if helper is not None and hasattr(helper, "build_voice_prompt"):
        return str(helper.build_voice_prompt(scene_profile, voice, sample_text, variant, tempo_hint))
    return (
        "请用普通话朗读下面正文，只朗读正文，不要读出任何说明。\n\n"
        "声音方向：自然中文短视频口播，清楚但不表演，避免播音腔、广告腔、叫卖感和夸张重音。\n\n"
        f"当前 voice: {voice}\n"
        f"{tempo_hint}\n\n"
        f"正文：\n{sample_text}\n"
    )


def fit_audio_to_duration(input_audio: Path, output_audio: Path, target_duration: float) -> dict[str, float]:
    helper = load_tts_builder_g_module()
    if helper is not None and hasattr(helper, "fit_audio_to_duration"):
        return helper.fit_audio_to_duration(input_audio, output_audio, target_duration)
    raw_duration = media_duration(input_audio) or target_duration
    tempo = raw_duration / target_duration if target_duration > 0 else 1.0
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    try:
        filters = f"aresample=48000,aformat=channel_layouts=stereo,atempo={max(0.5, min(2.0, tempo)):.6f},apad=pad_dur={target_duration:.6f},atrim=duration={target_duration:.6f},asetpts=N/SR/TB"
        run_cmd([find_binary("ffmpeg"), "-y", "-i", str(input_audio), "-af", filters, "-ar", "48000", "-ac", "2", str(output_audio)], timeout=180)
    except Exception:
        shutil.copyfile(input_audio, output_audio)
    return {"raw_duration": raw_duration, "target_duration": target_duration, "tempo": tempo, "fit_duration": media_duration(output_audio) or target_duration}


def cosine_score(left: Any | None, right: Any | None) -> float | None:
    if left is None or right is None:
        return None
    try:
        if np is not None and not hasattr(left, "numel"):
            return float(np.inner(left, right))
        import torch  # type: ignore

        score = torch.nn.functional.cosine_similarity(left.flatten(), right.flatten(), dim=0, eps=1e-6)
        return float(score.detach().cpu().item())
    except Exception:
        return None


def exp_similarity(left: float, right: float, scale: float) -> float:
    if left <= 0 or right <= 0:
        return 0.5
    return math.exp(-abs(left - right) / max(1e-6, scale))


def ratio_similarity(left: float, right: float) -> float:
    if left <= 0 or right <= 0:
        return 0.5
    ratio = min(left, right) / max(left, right)
    return max(0.0, min(1.0, ratio))


def catalog_item_voice(item: dict[str, Any]) -> str:
    return str(item.get("voice") or item.get("voice_id") or "").strip()


def catalog_item_audio_rel(item: dict[str, Any]) -> str:
    audio = item.get("audio") if isinstance(item.get("audio"), dict) else {}
    return str(item.get("sample_audio_path") or audio.get("path") or "").strip()


def catalog_item_audio_path(catalog_dir: Path, item: dict[str, Any]) -> Path:
    return catalog_dir / catalog_item_audio_rel(item)


def requested_catalog_voices(args: Args) -> set[str]:
    return {item.strip() for item in str(args.voices or "").split(",") if item.strip()}


def load_catalog(catalog_dir: Path, args: Args) -> dict[str, Any]:
    if not catalog_dir.exists() or not catalog_dir.is_dir():
        raise BlockedError("voice_catalog_missing", f"Voice catalog directory does not exist: {catalog_dir}")
    index_path = catalog_dir / "voice_catalog_index.json"
    if not index_path.exists():
        raise BlockedError("voice_catalog_index_missing", f"Voice catalog index is missing: {index_path}")
    payload = read_json(index_path)
    if not isinstance(payload, dict):
        raise BlockedError("voice_catalog_index_invalid", f"Voice catalog index must be a JSON object: {index_path}")
    provider = str(payload.get("provider") or "").strip().lower()
    model = str(payload.get("model") or "").strip()
    if provider not in {"google", "gemini"}:
        raise BlockedError("unsupported_catalog_provider", f"03_02 only supports Builder-G/Google catalog, got provider={provider}.")
    if str(args.provider or "google").strip().lower() not in {"google", "gemini"}:
        raise BlockedError("unsupported_provider", f"03_02 only supports Builder-G/Google, got provider={args.provider}.")
    if model and str(args.model or model).strip() != model:
        raise BlockedError("voice_catalog_model_mismatch", f"Catalog model={model} does not match requested model={args.model or model}.")
    if str(payload.get("sample_text_id") or "") != CATALOG_SAMPLE_TEXT_ID:
        raise BlockedError("voice_catalog_sample_text_mismatch", f"Voice catalog sample_text_id must be {CATALOG_SAMPLE_TEXT_ID}.")
    voices = payload.get("voices")
    if not isinstance(voices, list) or not voices:
        raise BlockedError("voice_catalog_empty", "Voice catalog has no voices.")
    requested_set = requested_catalog_voices(args)
    filtered = []
    for item in voices:
        if not isinstance(item, dict):
            continue
        voice = catalog_item_voice(item)
        if requested_set and voice not in requested_set:
            continue
        audio_path = catalog_item_audio_path(catalog_dir, item)
        if not voice or not audio_path.exists() or not audio_path.is_file():
            raise BlockedError(
                "voice_catalog_audio_missing",
                f"Required system voice catalog audio is missing for voice={voice}: {audio_path}. "
                "Generate and commit Analysis_V1 VoiceCatalog wav assets before deployment.",
            )
        if not voice:
            continue
        filtered.append(item)
    if len(filtered) < max(1, int(args.final_count)):
        raise BlockedError("voice_catalog_too_small", f"Voice catalog has only {len(filtered)} usable voices; need {args.final_count}.")
    payload["voices"] = filtered
    return payload


def score_voice(reference: dict[str, Any], candidate: dict[str, Any], resemblyzer_score: float | None, speechbrain_score: float | None) -> tuple[float, dict[str, float | None]]:
    energy_fit = exp_similarity(float(reference.get("rms") or 0.0), float(candidate.get("rms") or 0.0), 0.12)
    zcr_fit = exp_similarity(float(reference.get("zero_crossing") or 0.0), float(candidate.get("zero_crossing") or 0.0), 0.08)
    centroid_fit = ratio_similarity(float(reference.get("spectral_centroid") or 0.0), float(candidate.get("spectral_centroid") or 0.0))
    pitch_fit = ratio_similarity(float(reference.get("pitch_hz") or 0.0), float(candidate.get("pitch_hz") or 0.0))
    tempo_fit = ratio_similarity(float(reference.get("speaking_rate_cps") or 0.0), float(candidate.get("speaking_rate_cps") or 0.0))
    signal_quality = min(float(candidate.get("signal_quality") or 0.0), float(reference.get("signal_quality") or 0.0))
    acoustic_similarity = 0.30 * energy_fit + 0.25 * centroid_fit + 0.20 * zcr_fit + 0.15 * pitch_fit + 0.10 * signal_quality
    embedding_scores = [value for value in (speechbrain_score, resemblyzer_score) if isinstance(value, (int, float))]
    embedding_fit = sum(float(value) for value in embedding_scores) / len(embedding_scores) if embedding_scores else acoustic_similarity
    speechbrain_fit = float(speechbrain_score) if isinstance(speechbrain_score, (int, float)) else embedding_fit
    resemblyzer_fit = float(resemblyzer_score) if isinstance(resemblyzer_score, (int, float)) else embedding_fit
    score = (
        0.30 * speechbrain_fit
        + 0.25 * resemblyzer_fit
        + 0.15 * pitch_fit
        + 0.12 * tempo_fit
        + 0.08 * acoustic_similarity
        + 0.05 * signal_quality
        + 0.05 * 1.0
    )
    return score, {
        "speechbrain_cosine": speechbrain_score,
        "resemblyzer_cosine": resemblyzer_score,
        "acoustic_similarity": acoustic_similarity,
        "pitch_fit": pitch_fit,
        "tempo_fit": tempo_fit,
        "pause_pattern_fit": None,
        "energy_fit": energy_fit,
        "zero_crossing_fit": zcr_fit,
        "spectral_centroid_fit": centroid_fit,
        "signal_quality": signal_quality,
        "catalog_stability": 1.0,
    }


def session_candidate_path(rank: int) -> str:
    return f"{SESSION_TTS_DIR_REL}/tts_builder_candidate_{rank:03d}.wav"


def build_final_payload(window: dict[str, Any], reference_profile: dict[str, Any], final_rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for rank, row in enumerate(final_rows, 1):
        candidates.append({
            "rank": rank,
            "candidate_id": f"tts_{rank:03d}",
            "provider": row.get("provider"),
            "model": row.get("model"),
            "voice": row.get("voice"),
            "voice_label": row.get("voice_label") or row.get("voice"),
            "selected": rank == 1,
            "prompt": row.get("prompt") or "",
            "generation_prompt": row.get("prompt") or "",
            "tts_builder_prompt": row.get("prompt") or "",
            "prompt_path": row.get("prompt_path") or "",
            "prompt_source": "Prompt",
            "prompt_sha256": row.get("prompt_sha256") or "",
            "sample_audio_path": session_candidate_path(rank),
            "catalog_audio_path": row.get("catalog_audio_path"),
            "tempo": row.get("tempo"),
            "tempo_source": row.get("tempo_source") or "measured_after_raw_tts_generation",
            "raw_duration": row.get("raw_duration"),
            "target_duration": row.get("target_duration"),
            "fit_duration": row.get("fit_duration"),
            "needs_review": False,
            "score": row.get("score"),
            "score_parts": row.get("score_parts"),
            "catalog_rank": row.get("catalog_rank"),
            "catalog_score": row.get("catalog_score"),
            "catalog_tempo_prior": row.get("catalog_tempo_prior"),
            "target_gender": row.get("target_gender"),
            "candidate_gender": row.get("candidate_gender"),
            "gender_match": row.get("gender_match"),
            "raw_audio": row.get("raw_audio") or "",
            "gemini_meta": row.get("gemini_meta") or {},
            "reason": "Voice was pre-ranked by local catalog similarity, then regenerated by Gemini TTS with the real selected SRT text and fitted to the target duration.",
        })
    selected = candidates[0] if candidates else {}
    return {
        "schema_version": "analysis_v1_tts_builder_g_candidates_0.1",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "sample_policy": {
            "selected_duration": window.get("duration"),
            "tested_durations": [16],
            "selected_range": {"start": window.get("start"), "end": window.get("end")},
            "reason": "03_02 uses normal-speed fixed 16s voice catalog samples to pre-rank voices, then tries a bounded ranked pool until the required final candidates are generated.",
        },
        "scene_profile": {
            "source": "local_voice_catalog",
            "scene_type": "tts_voice_quick_match",
            "voice_prompt_guidance": {},
        },
        "reference_audio_profile": reference_profile,
        "selected_candidate_id": selected.get("candidate_id", ""),
        "selected_candidate": selected,
        "selected_generation_prompt": "",
        "selected_tts_builder_prompt": "",
        "selected_prompt_path": "",
        "selected_prompt_sha256": "",
        "candidates": candidates,
        "created_at": now_iso(),
    }


def generate_model_candidate(
    workspace: Path,
    args: Args,
    api_key: str,
    model: str,
    scene_profile: dict[str, Any],
    reference_text: str,
    row: dict[str, Any],
    rank: int,
    target_duration: float,
) -> tuple[dict[str, Any], int]:
    voice = str(row.get("voice") or "").strip()
    variant = "closest_reference" if rank == 1 else "natural_selfie"
    prompt_text = build_model_prompt(scene_profile, voice, reference_text, float(row.get("tempo") or 1.0), variant)
    safe_voice = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in voice) or f"voice_{rank}"
    prompt_rel = f"{PROMPT_DIR_REL}/final_candidate_{rank:03d}_{safe_voice}_prompt.txt"
    raw_rel = f"{WORKING_RAW_DIR_REL}/final_candidate_{rank:03d}_{safe_voice}_raw.wav"
    fit_rel = f"{WORKING_FIT_DIR_REL}/final_candidate_{rank:03d}_{safe_voice}_fit.wav"
    prompt_path = workspace / prompt_rel
    raw_path = workspace / raw_rel
    fit_path = workspace / fit_rel
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_text, encoding="utf-8")
    model_calls = 0
    if raw_path.exists() and raw_path.stat().st_size > 0 and not args.force:
        gemini_meta = {"duration": media_duration(raw_path), "cached": True}
    else:
        gemini_meta = call_gemini_tts(api_key, model, voice, prompt_path, raw_path, workspace=workspace, asset_key=f"final_candidate_{rank:03d}_{safe_voice}")
        model_calls = 1
    fit_meta = fit_audio_to_duration(raw_path, fit_path, target_duration)
    shutil.copyfile(fit_path, workspace / session_candidate_path(rank))
    raw_duration = float(fit_meta.get("raw_duration") or media_duration(raw_path) or target_duration)
    generated = {
        **row,
        "catalog_rank": row.get("rank"),
        "catalog_score": row.get("score"),
        "catalog_tempo_prior": row.get("tempo"),
        "prompt": prompt_text,
        "prompt_path": prompt_rel,
        "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
        "raw_audio": raw_rel,
        "fit_audio": fit_rel,
        "gemini_meta": gemini_meta,
        "raw_duration": round(raw_duration, 3),
        "target_duration": round(float(target_duration), 3),
        "fit_duration": round(float(fit_meta.get("fit_duration") or media_duration(fit_path) or target_duration), 3),
        "tempo": round(float(fit_meta.get("tempo") or (raw_duration / target_duration if target_duration > 0 else 1.0)), 6),
        "tempo_source": "measured_after_raw_tts_generation",
        "model_call_made": bool(model_calls),
    }
    return generated, model_calls


def generate_ranked_model_candidates(
    workspace: Path,
    args: Args,
    api_key: str,
    model: str,
    scene_profile: dict[str, Any],
    reference_text: str,
    eligible_rows: list[dict[str, Any]],
    target_duration: float,
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], int, int, int]:
    final_count = max(1, int(args.final_count or 3))
    pool_limit = max(final_count, final_count * 2)
    generation_pool = eligible_rows[:pool_limit]
    if len(generation_pool) < final_count:
        raise BlockedError(
            "voice_catalog_gender_candidates_too_small",
            f"Only {len(generation_pool)} gender-matched candidates were ranked; need {final_count}.",
        )

    final_rows: list[dict[str, Any]] = []
    model_calls = 0
    attempted = 0
    generation_warnings: list[dict[str, Any]] = []
    for source_index, row in enumerate(generation_pool, 1):
        if len(final_rows) >= final_count:
            break
        attempted += 1
        output_rank = len(final_rows) + 1
        try:
            generated_row, calls = generate_model_candidate(
                workspace,
                args,
                api_key,
                model,
                scene_profile,
                reference_text,
                row,
                output_rank,
                target_duration,
            )
        except BlockedError:
            raise
        except ToolError as exc:
            if not is_candidate_local_tts_error(exc):
                raise
            voice = str(row.get("voice") or "").strip()
            source_rank = row.get("rank") or source_index
            error_code = "provider_invalid_argument" if is_gemini_invalid_argument_error(exc) else "tts_response_without_audio"
            generation_warnings.append({
                "code": "tts_candidate_generation_failed",
                "message": f"Skipped voice={voice or 'unknown'} after candidate-local TTS failure: {str(exc)[:500]}",
                "error_code": error_code,
                "voice": voice,
                "provider": str(row.get("provider") or "google"),
                "model": str(row.get("model") or model),
                "source_rank": source_rank,
                "output_rank": output_rank,
            })
            continue
        generated_row["generation_source_rank"] = row.get("rank") or source_index
        final_rows.append(generated_row)
        model_calls += calls

    if generation_warnings:
        result.setdefault("warnings", []).extend(generation_warnings)
    result.setdefault("counts", {}).update({
        "candidate_attempts": attempted,
        "candidate_failures": len(generation_warnings),
        "candidate_pool_limit": pool_limit,
    })
    if len(final_rows) < final_count:
        raise CandidatePoolExhaustedError(
            generated=len(final_rows),
            required=final_count,
            attempted=attempted,
            failures=len(generation_warnings),
        )
    return final_rows, model_calls, attempted, len(generation_warnings)


def run_builder(workspace: Path, args: Args, variables: dict[str, Any], final_items_payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs(workspace)
    catalog_dir = resolve_catalog_dir(workspace, args)
    catalog = load_catalog(catalog_dir, args)
    items = [item for item in final_items_payload.get("items", []) if isinstance(item, dict) and dialogue(item)]
    target_duration = float(args.reference_duration or 16.0) if float(args.reference_duration or 0.0) > 0 else 16.0
    window = forced_sample_window(items, float(args.reference_start or 0.0), target_duration) if float(args.reference_duration or 0.0) > 0 else choose_sample_window(items, target_duration)
    reference_audio = extract_reference_audio(workspace, float(window.get("start") or 0.0), float(window.get("duration") or target_duration))
    reference_text = selected_dialogue(window.get("items") or [])
    reference_rate = count_cjk(reference_text) / max(0.1, float(window.get("duration") or target_duration))
    reference_features = audio_features(reference_audio, reference_rate)
    if float(reference_features.get("signal_quality") or 0.0) <= 0.05:
        raise BlockedError("reference_audio_voice_too_weak", "Reference audio has too little usable voice signal for local matching.")
    scene_profile = load_scene_profile(workspace, variables, reference_text)
    target_gender = infer_target_gender(scene_profile, reference_features)
    resemblyzer_backend = load_resemblyzer_backend(result)
    speechbrain_backend = load_speechbrain_backend(result)
    reference_resemblyzer = resemblyzer_embedding(reference_audio, resemblyzer_backend)
    reference_speechbrain = speechbrain_embedding(reference_audio, speechbrain_backend)
    reference_profile = {
        "audio_path": relpath(reference_audio, workspace),
        "selected_range": {"start": window.get("start"), "end": window.get("end")},
        "selected_duration": window.get("duration"),
        "dialogue_chars": count_cjk(reference_text),
        "dialogue": reference_text,
        "features": rounded_feature_map(reference_features),
        "gender_gate": target_gender,
        "embedding_backends": {
            "resemblyzer": bool(reference_resemblyzer is not None),
            "speechbrain": bool(reference_speechbrain is not None),
        },
    }
    result["local_match_backend"] = {
        "resemblyzer": bool(reference_resemblyzer is not None),
        "speechbrain": bool(reference_speechbrain is not None),
        "acoustic_features": True,
        "fallback_mode": "speechbrain_resemblyzer_acoustic" if reference_speechbrain is not None and reference_resemblyzer is not None else "resemblyzer_acoustic" if reference_resemblyzer is not None else "acoustic_only",
    }
    write_json(workspace / OUTPUT_REFERENCE_PROFILE_REL, reference_profile)

    rows = []
    sample_text = str(catalog.get("sample_text") or "")
    sample_text_chars = count_cjk(sample_text)
    for item in catalog.get("voices") or []:
        voice = str(item.get("voice") or item.get("voice_id") or "").strip()
        audio_rel = str(item.get("sample_audio_path") or item.get("audio", {}).get("path") or "")
        audio_path = catalog_dir / audio_rel
        raw_duration = float(item.get("raw_duration") or item.get("audio", {}).get("duration") or wav_duration(audio_path) or 16.0)
        catalog_rate = sample_text_chars / max(0.1, raw_duration)
        features = audio_features(audio_path, catalog_rate)
        candidate_resemblyzer = resemblyzer_embedding(audio_path, resemblyzer_backend)
        candidate_speechbrain = speechbrain_embedding(audio_path, speechbrain_backend)
        resemblyzer_score = cosine_score(reference_resemblyzer, candidate_resemblyzer)
        speechbrain_score = cosine_score(reference_speechbrain, candidate_speechbrain)
        score, parts = score_voice(reference_features, features, resemblyzer_score, speechbrain_score)
        tempo = (catalog_rate / reference_rate) if reference_rate > 0 and catalog_rate > 0 else 1.0
        candidate_gender = catalog_voice_gender(item, features)
        gender_ok = gender_match(target_gender, candidate_gender)
        rows.append({
            "rank": 0,
            "provider": item.get("provider") or catalog.get("provider") or "google",
            "model": item.get("model") or catalog.get("model") or str(args.model or DEFAULT_TTS_MODEL),
            "voice": voice,
            "voice_label": item.get("voice_label") or item.get("label") or voice,
            "catalog_audio_abs": str(audio_path),
            "catalog_audio_path": relpath(audio_path, workspace),
            "raw_duration": round(raw_duration, 3),
            "target_duration": round(float(window.get("duration") or 16.0), 3),
            "fit_duration": round(wav_duration(audio_path), 3),
            "tempo": round(float(tempo), 6),
            "tempo_source": "local_voice_catalog_match",
            "score": round(float(score), 6),
            "score_parts": {key: (round(float(value), 6) if isinstance(value, (int, float)) else value) for key, value in parts.items()},
            "target_gender": target_gender.get("target_gender"),
            "candidate_gender": candidate_gender,
            "gender_match": gender_ok,
            "exclude_reason": "" if gender_ok else f"gender_mismatch:{target_gender.get('target_gender')}!={candidate_gender.get('gender') or 'unknown'}",
            "features": rounded_feature_map(features),
            "embedding_backends": {
                "resemblyzer": bool(candidate_resemblyzer is not None),
                "speechbrain": bool(candidate_speechbrain is not None),
            },
            "catalog_index_item": item,
        })
    ranked = sorted(rows, key=lambda row: float(row.get("score") or 0.0), reverse=True)
    for catalog_rank, row in enumerate(ranked, 1):
        row["rank"] = catalog_rank
    eligible_rows = [row for row in ranked if bool(row.get("gender_match", True))]
    final_count = max(1, int(args.final_count or 3))
    if len(eligible_rows) < final_count:
        raise BlockedError(
            "voice_catalog_gender_candidates_too_small",
            f"Only {len(eligible_rows)} gender-matched candidates were ranked for target_gender={target_gender.get('target_gender')}; need {final_count}.",
        )
    provider = str(eligible_rows[0].get("provider") or catalog.get("provider") or "google").strip().lower()
    model = str(eligible_rows[0].get("model") or catalog.get("model") or args.model or DEFAULT_TTS_MODEL).strip()
    api_key = load_tts_api_key(args, provider, model)
    if not api_key:
        raise BlockedError("gemini_api_key_missing", f"No enabled Google/Gemini TTS API key found for model={model}.")
    final_rows, model_calls, candidate_attempts, candidate_failures = generate_ranked_model_candidates(
        workspace,
        args,
        api_key,
        model,
        scene_profile,
        reference_text,
        eligible_rows,
        float(window.get("duration") or 16.0),
        result,
    )
    final_payload = build_final_payload(window, reference_profile, final_rows)
    final_payload["scene_profile"] = scene_profile
    write_json(workspace / OUTPUT_CATALOG_AUDIT_REL, {
        "catalog_dir": str(catalog_dir),
        "catalog_schema_version": catalog.get("schema_version"),
        "embedding_backends": {
            "resemblyzer": bool(resemblyzer_backend is not None),
            "speechbrain": bool(speechbrain_backend is not None),
        },
        "local_match_backend": result.get("local_match_backend") or {},
        "reference_profile": reference_profile,
        "gender_gate": {
            "target": target_gender,
            "eligible_count": len(eligible_rows),
            "excluded": [
                {
                    "voice": row.get("voice"),
                    "rank": row.get("rank"),
                    "score": row.get("score"),
                    "candidate_gender": row.get("candidate_gender"),
                    "exclude_reason": row.get("exclude_reason"),
                }
                for row in ranked
                if not bool(row.get("gender_match", True))
            ],
        },
        "ranked": ranked,
        "generated_top": final_rows,
    })
    write_json(workspace / OUTPUT_FINAL_REL, final_payload)
    write_json(workspace / SESSION_TTS_FINAL_REL, final_payload)
    write_json(workspace / WORKING_STATE_REL, {
        "tool": TOOL_NAME,
        "status": "completed",
        "phase": "finalize",
        "model_calls": model_calls,
        "outputs": {"tts_builder_candidates": SESSION_TTS_FINAL_REL},
        "updated_at": now_iso(),
    })
    result["status"] = "completed"
    result["inputs"] = {
        "variables": VARIABLES_REL,
        "final_srt_frame_items": SESSION_FINAL_ITEMS_REL,
        "reference_audio": SESSION_AUDIO_REFERENCE_REL,
        "voice_catalog_dir": str(catalog_dir),
    }
    result["outputs"] = {
        "tts_builder_candidates": SESSION_TTS_FINAL_REL,
        "candidate_audio_001": session_candidate_path(1),
        "candidate_audio_002": session_candidate_path(2),
        "candidate_audio_003": session_candidate_path(3),
    }
    result["counts"] = {
        "model_calls": model_calls,
        "candidate_attempts": candidate_attempts,
        "candidate_failures": candidate_failures,
        "candidate_pool_limit": final_count * 2,
        "catalog_voices": len(catalog.get("voices") or []),
        "ranked_candidates": len(ranked),
        "final_candidates": len(final_rows),
    }
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_FINAL_ITEMS_REL,
        WORKING_REFERENCE_REL,
        OUTPUT_REFERENCE_PROFILE_REL,
        OUTPUT_CATALOG_AUDIT_REL,
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
        for rel in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", f"{TOOL_DIR_NAME}/Report", SESSION_TTS_DIR_REL):
            result["prepared_directories"].append(rel)
        variables = load_variables(workspace)
        final_items = load_final_items(workspace)
        write_json(workspace / WORKING_VARIABLES_REL, variables)
        write_json(workspace / WORKING_FINAL_ITEMS_REL, final_items)
        if args.resume and (workspace / SESSION_TTS_FINAL_REL).exists() and not args.force:
            final_payload = read_json(workspace / SESSION_TTS_FINAL_REL)
            result["status"] = "completed"
            result["outputs"] = {"tts_builder_candidates": SESSION_TTS_FINAL_REL}
            result["counts"] = {"final_candidates": len(final_payload.get("candidates") or []), "reused": 1}
            result["warnings"].append({"code": "reused_completed_output", "message": "Existing TTS Builder candidates were reused."})
        else:
            run_builder(workspace, args, variables, final_items, result)
    except BlockedError as exc:
        add_block(result, exc.code, exc.message)
    except PermissionError as exc:
        add_block(result, "workspace_permission_denied", f"Cannot read/write Analysis_V1 workspace. Original error: {exc}")
    except Exception as exc:
        add_failure(result, exc)
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
    parser = argparse.ArgumentParser(description="Build three Builder-G/Gemini TTS voice candidates from a local fixed voice catalog.")
    parser.add_argument("--workspace", default="", help="Analysis_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--voice-catalog-dir", default="", help="Flat Gemini voice catalog directory with voice_catalog_index.json.")
    parser.add_argument("--provider", default="google")
    parser.add_argument("--model", default=DEFAULT_TTS_MODEL)
    parser.add_argument("--voices", default="", help="Optional comma-separated voice allowlist.")
    parser.add_argument("--reference-start", type=float, default=0.0)
    parser.add_argument("--reference-duration", type=float, default=0.0)
    parser.add_argument("--final-count", type=int, default=3)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        voice_catalog_dir=str(ns.voice_catalog_dir or ""),
        provider=str(ns.provider or "google"),
        model=str(ns.model or DEFAULT_TTS_MODEL),
        voices=str(ns.voices or ""),
        reference_start=float(ns.reference_start),
        reference_duration=float(ns.reference_duration),
        final_count=int(ns.final_count),
        database_url=str(ns.database_url or ""),
        database_url_env=str(ns.database_url_env or DEFAULT_DATABASE_URL_ENV),
        force=bool(ns.force),
        resume=bool(ns.resume),
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
