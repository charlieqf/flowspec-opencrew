#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_CONFIG_ROOT = REPO_ROOT / "ModelConfig" / "backend"
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
if str(MODEL_CONFIG_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_CONFIG_ROOT))

from opcrew_model_config.media_model_config import audio_url_bytes, dashscope_tts_preview_url, media_options  # noqa: E402


DEFAULT_MODEL = "qwen3-tts-flash"
SAMPLE_TEXT_ID = "fixed_cn_v1"
GEMINI_CATALOG_INDEX = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "VoiceCatalog" / "gemini-3.1-flash-tts-preview" / "voice_catalog_index.json"


def read_sample_text() -> str:
    payload = json.loads(GEMINI_CATALOG_INDEX.read_text(encoding="utf-8"))
    return str(payload.get("sample_text") or "").strip()


def ffmpeg_bin() -> str:
    for candidate in (
        os.environ.get("OPENCREW_FFMPEG_PATH", ""),
        str(REPO_ROOT / "ToolLibrary" / ".bin" / "ffmpeg"),
        str(REPO_ROOT / "backend" / ".venv" / "bin" / "static_ffmpeg"),
    ):
        if candidate and Path(candidate).exists():
            return candidate
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise RuntimeError("ffmpeg is required to normalize Qwen catalog audio.")


def safe_voice_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value or "").strip())[:96] or "voice"


def wav_meta(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as reader:
        frames = reader.getnframes()
        rate = reader.getframerate()
        return {
            "path": path.name,
            "duration": round(frames / float(rate or 1), 3),
            "sample_rate": rate,
            "channels": reader.getnchannels(),
            "sample_width_bytes": reader.getsampwidth(),
            "frames": frames,
            "format": "wav",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }


def qwen_model_option(model: str) -> dict[str, Any]:
    providers = {item["provider"]: item for item in media_options("tts")}
    qwen = providers.get("qwen") or {}
    models = {item["model"]: item for item in qwen.get("models", [])}
    if model not in models:
        raise RuntimeError(f"Unknown Qwen TTS model: {model}")
    return models[model]


def resolve_api_key(args: argparse.Namespace) -> str:
    value = (args.api_key or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or os.environ.get("OPENCREW_TTS_API_KEY") or "").strip()
    if value:
        return value
    if args.api_key_ref:
        from opcrew_backend.config import load_config
        from opcrew_backend.context import AppContext

        ctx = AppContext(load_config())
        return str(ctx.secret_store.get(args.api_key_ref) or "").strip()
    return ""


def normalize_audio(data: bytes, mime: str, output_path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source_ext = ".mp3" if "mpeg" in mime.lower() or "mp3" in mime.lower() else ".wav"
        source = Path(tmp) / f"source{source_ext}"
        source.write_bytes(data)
        cmd = [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(source),
            "-t",
            "16",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)


def qwen_audio_url_bytes(audio_url: str) -> tuple[bytes, str]:
    value = audio_url.strip()
    if not value.startswith("http://"):
        return audio_url_bytes(value)
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").lower()
    if not (host.startswith("dashscope-result-") and host.endswith(".aliyuncs.com")):
        return audio_url_bytes(value)
    request = urllib.request.Request(value, headers={"User-Agent": "OpenCrew/qwen-catalog-generator"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(25 * 1024 * 1024 + 1)
        mime = response.headers.get("Content-Type", "audio/wav")
    if len(data) > 25 * 1024 * 1024:
        raise RuntimeError("Qwen audio download exceeded 25MB limit.")
    return data, mime


def build_voice_row(args: argparse.Namespace, api_key: str, sample_text: str, out_dir: Path, voice: dict[str, Any]) -> dict[str, Any]:
    voice_id = str(voice.get("voice_id") or "").strip()
    if not voice_id or str(voice.get("mode") or "") == "custom_voice_id":
        return {}
    filename = f"{safe_voice_token(voice_id)}_{SAMPLE_TEXT_ID}_16s.wav"
    output = out_dir / filename
    if args.dry_run:
        meta = {"path": filename, "duration": 16.0, "sample_rate": 24000, "channels": 1, "format": "wav", "sha256": ""}
    elif output.exists() and output.stat().st_size > 0 and not args.force:
        meta = wav_meta(output)
    else:
        audio_url = dashscope_tts_preview_url(api_key, "qwen", args.model, voice_id, sample_text, "", "zh", "direct")
        data, mime = qwen_audio_url_bytes(audio_url)
        normalize_audio(data, mime, output)
        meta = wav_meta(output)
    return {
        "voice": voice_id,
        "voice_id": voice_id,
        "voice_label": str(voice.get("label") or voice_id),
        "provider": "qwen",
        "model": args.model,
        "voice_mode": str(voice.get("mode") or "preset"),
        "language": str(voice.get("language") or "zh"),
        "gender": str(voice.get("gender") or ""),
        "style": str(voice.get("style") or ""),
        "sample_text_id": SAMPLE_TEXT_ID,
        "sample_audio_path": filename,
        "raw_duration": meta["duration"],
        "clip_policy": "normal_speed_truncate_first_16s",
        "speed_adjustment": 1.0,
        "audio": meta,
        "matching": {
            "resemblyzer_embedding_path": "",
            "speechbrain_embedding_path": "",
            "profile_path": "",
            "features_ready": False,
            "recommended_use": "voice_catalog_similarity_candidate",
        },
    }


def build_catalog(args: argparse.Namespace) -> dict[str, Any]:
    api_key = resolve_api_key(args)
    if not api_key and not args.dry_run:
        raise RuntimeError("Set DASHSCOPE_API_KEY or QWEN_API_KEY before generating Qwen catalog audio.")
    sample_text = args.sample_text.strip() or read_sample_text()
    sample_hash = hashlib.sha256(sample_text.encode("utf-8")).hexdigest()
    model_option = qwen_model_option(args.model)
    voice_allow = {item.strip() for item in args.voices.split(",") if item.strip()}
    voices = [item for item in model_option.get("voices", []) if not voice_allow or str(item.get("voice_id") or "") in voice_allow]
    if args.limit:
        voices = voices[: max(1, int(args.limit))]
    out_dir = Path(args.output_dir or REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "VoiceCatalog" / args.model)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows_by_index: dict[int, dict[str, Any]] = {}
    failed: list[dict[str, str]] = []

    def run_one(index: int, voice: dict[str, Any]) -> tuple[int, dict[str, Any] | None, dict[str, str] | None]:
        voice_id = str(voice.get("voice_id") or "").strip()
        try:
            return index, build_voice_row(args, api_key, sample_text, out_dir, voice), None
        except Exception as exc:
            if not args.continue_on_error:
                raise
            return index, None, {"voice": voice_id, "error": str(exc)}

    jobs = max(1, int(args.jobs or 1))
    if jobs == 1:
        for index, voice in enumerate(voices):
            row_index, row, error = run_one(index, voice)
            if row:
                rows_by_index[row_index] = row
            if error:
                failed.append(error)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = [executor.submit(run_one, index, voice) for index, voice in enumerate(voices)]
            for future in concurrent.futures.as_completed(futures):
                row_index, row, error = future.result()
                if row:
                    rows_by_index[row_index] = row
                if error:
                    failed.append(error)

    rows = [rows_by_index[index] for index in sorted(rows_by_index)]

    payload = {
        "schema_version": "analysis_v1_voice_catalog_index_0.3",
        "catalog_id": f"qwen_{args.model}_{SAMPLE_TEXT_ID}",
        "provider": "qwen",
        "model": args.model,
        "sample_text_id": SAMPLE_TEXT_ID,
        "sample_text": sample_text,
        "sample_text_sha256": sample_hash,
        "audio_filename_pattern": "{voice}_fixed_cn_v1_16s.wav",
        "clip_policy": "normal_speed_truncate_first_16s",
        "speed_adjustment": 1.0,
        "count": len(rows),
        "failed": failed,
        "voices": rows,
    }
    (out_dir / "voice_catalog_index.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "output_dir": str(out_dir), "model": args.model, "count": len(rows), "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Analysis_V1 Qwen TTS VoiceCatalog wav samples.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--voices", default="", help="Optional comma-separated voice_id allowlist.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sample-text", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-ref", default="", help="Read API key from OpenCrew local secret store, for example tts_qwen_key.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--jobs", type=int, default=1, help="Concurrent voice generation jobs. Default 1.")
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args()
    result = build_catalog(args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
