from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_binaries import find_ffmpeg, media_env


TOOL_NAME = "AudioASRPipeline"
TOOL_VERSION = "0.2.0"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_PROVIDER = "local_whisper"
DEFAULT_MODEL = "small"
DEFAULT_LANGUAGE = "zh"
DEFAULT_CONFIG_TABLE = "tool_asr_provider_configs"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
DEFAULT_ALIYUN_FUN_ASR_URL = "dashscope://audio/asr/recognition"
ALIYUN_FUN_ASR_MODEL_ALIASES = {
    "fun-asr": "fun-asr-realtime",
}
TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
from opencrew_runtime_secrets import resolve_secret_value, store_secret_value


def normalize_aliyun_fun_asr_model(model: str) -> str:
    return ALIYUN_FUN_ASR_MODEL_ALIASES.get(model, model)


@dataclass(frozen=True)
class PipelinePaths:
    workspace: Path
    audio_dir: Path
    meta_dir: Path
    transcripts_dir: Path
    audio_path: Path


@dataclass(frozen=True)
class ASRConfig:
    name: str
    provider: str
    model: str
    language: str
    api_url: str
    api_key_ciphertext: str
    api_key_ref: str
    extra_json: dict[str, Any]
    source: str
    warnings: list[str]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, env=media_env())


def rel_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def load_json_text(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def import_postgres_driver() -> tuple[str, Any] | tuple[None, None]:
    try:
        import psycopg  # type: ignore

        return "psycopg", psycopg
    except Exception:
        pass
    try:
        import psycopg2  # type: ignore

        return "psycopg2", psycopg2
    except Exception:
        return None, None


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def db_row_to_config(row: Any, source: str, warnings: list[str]) -> ASRConfig:
    if isinstance(row, dict):
        data = row
    else:
        keys = ["name", "provider", "model", "language", "api_url", "api_key_ciphertext", "api_key_ref", "extra_json"]
        data = {key: row[index] if index < len(row) else None for index, key in enumerate(keys)}

    def text_value(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value or "")

    api_key_ref = text_value(data.get("api_key_ref"))
    legacy_key = text_value(data.get("api_key_ciphertext"))

    return ASRConfig(
        name=text_value(data.get("name")) or "local_whisper_default",
        provider=text_value(data.get("provider")) or DEFAULT_PROVIDER,
        model=normalize_aliyun_fun_asr_model(text_value(data.get("model"))) or DEFAULT_MODEL,
        language=text_value(data.get("language")) or DEFAULT_LANGUAGE,
        api_url=text_value(data.get("api_url")),
        api_key_ciphertext=resolve_secret_value(api_key_ref, legacy_key),
        api_key_ref=api_key_ref,
        extra_json=load_json_text(data.get("extra_json")),
        source=source,
        warnings=warnings,
    )


def fallback_config(warnings: list[str] | None = None) -> ASRConfig:
    return ASRConfig(
        name="fallback_local_whisper",
        provider=DEFAULT_PROVIDER,
        model=DEFAULT_MODEL,
        language=DEFAULT_LANGUAGE,
        api_url="",
        api_key_ciphertext="",
        api_key_ref="",
        extra_json={},
        source="fallback",
        warnings=warnings or [],
    )


def load_asr_config_from_postgres(database_url: str, config_name: str | None, table_name: str) -> ASRConfig:
    warnings: list[str] = []
    driver_name, driver = import_postgres_driver()
    if driver is None:
        return fallback_config(["PostgreSQL driver not available; using fallback local_whisper config"])
    try:
        conn = driver.connect(normalize_database_url(database_url))
    except Exception as exc:
        return fallback_config([f"Failed to connect PostgreSQL; using fallback local_whisper config: {exc}"])

    try:
        cursor = conn.cursor()
        if config_name:
            cursor.execute(
                f"SELECT name, provider, model, language, api_url, api_key_ciphertext, api_key_ref, extra_json FROM {table_name} WHERE name = %s AND enabled = true LIMIT 1",
                (config_name,),
            )
        else:
            cursor.execute(
                f"SELECT name, provider, model, language, api_url, api_key_ciphertext, api_key_ref, extra_json FROM {table_name} WHERE enabled = true ORDER BY priority ASC, id ASC LIMIT 1"
            )
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            suffix = f" for config '{config_name}'" if config_name else ""
            return fallback_config([f"No enabled ASR provider config found{suffix}; using fallback local_whisper config"])
        return db_row_to_config(row, f"postgres:{table_name}", warnings)
    except Exception as exc:
        return fallback_config([f"Failed to read ASR provider config; using fallback local_whisper config: {exc}"])
    finally:
        try:
            conn.close()
        except Exception:
            pass


def resolve_asr_config(args: argparse.Namespace) -> ASRConfig:
    warnings: list[str] = []
    database_url = os.environ.get(str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL
    if database_url:
        config = load_asr_config_from_postgres(database_url, args.config_name, args.config_table)
    else:
        config = fallback_config([f"{args.database_url_env or DEFAULT_DATABASE_URL_ENV} is not set; using fallback local_whisper config"])

    provider = args.provider or config.provider
    model = args.model or config.model
    language = args.language or config.language
    api_url = args.api_url or config.api_url
    if args.provider or args.model or args.language or args.api_url:
        warnings.extend(config.warnings)
        return ASRConfig(
            name=config.name,
            provider=provider,
            model=model,
            language=language,
            api_url=api_url,
            api_key_ciphertext=config.api_key_ciphertext,
            api_key_ref=config.api_key_ref,
            extra_json=config.extra_json,
            source=f"{config.source}+cli_override",
            warnings=warnings,
        )
    return config


def resolve_paths(args: argparse.Namespace) -> PipelinePaths:
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
    audio_dir = Path(args.audio_dir).expanduser().resolve() if args.audio_dir else workspace / "audio"
    meta_dir = Path(args.meta_dir).expanduser().resolve() if args.meta_dir else workspace / "meta"
    transcripts_dir = Path(args.transcripts_dir).expanduser().resolve() if args.transcripts_dir else workspace / "transcripts"
    audio_path = Path(args.audio_output).expanduser().resolve() if args.audio_output else audio_dir / "reference_audio.wav"
    return PipelinePaths(workspace=workspace, audio_dir=audio_dir, meta_dir=meta_dir, transcripts_dir=transcripts_dir, audio_path=audio_path)


def validate_table_name(table_name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
        raise ValueError(f"invalid table name: {table_name}")
    return table_name


def postgres_connect(database_url: str) -> Any:
    driver_name, driver = import_postgres_driver()
    if driver is None:
        raise RuntimeError("PostgreSQL driver is not available. Install psycopg[binary] in the OpenCrew runtime.")
    return driver.connect(normalize_database_url(database_url))


def init_config_table(database_url: str, table_name: str) -> None:
    table_name = validate_table_name(table_name)
    conn = postgres_connect(database_url)
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
CREATE TABLE IF NOT EXISTS {table_name} (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  priority INTEGER NOT NULL DEFAULT 100,
  model TEXT NOT NULL DEFAULT 'small',
  language TEXT DEFAULT 'zh',
  api_url TEXT,
  api_key_ciphertext TEXT,
  api_key_ref TEXT,
  extra_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""")
        conn.commit()
        cursor.close()
    finally:
        conn.close()


def upsert_provider_config(
    database_url: str,
    table_name: str,
    name: str,
    provider: str,
    model: str,
    language: str,
    api_url: str,
    api_key: str,
    api_key_ref: str,
    priority: int,
    extra_json: dict[str, Any],
) -> None:
    table_name = validate_table_name(table_name)
    init_config_table(database_url, table_name)
    resolved_api_key_ref = api_key_ref or ("aliyun_bailian_fun_asr_key" if provider == "aliyun_bailian_fun_asr" else f"{provider}_key")
    if api_key:
        store_secret_value(resolved_api_key_ref, api_key)
    conn = postgres_connect(database_url)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
INSERT INTO {table_name} (name, provider, enabled, priority, model, language, api_url, api_key_ciphertext, api_key_ref, extra_json, updated_at)
VALUES (%s, %s, true, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
ON CONFLICT (name) DO UPDATE SET
  provider = EXCLUDED.provider,
  enabled = EXCLUDED.enabled,
  priority = EXCLUDED.priority,
  model = EXCLUDED.model,
  language = EXCLUDED.language,
  api_url = EXCLUDED.api_url,
  api_key_ciphertext = NULL,
  api_key_ref = EXCLUDED.api_key_ref,
  extra_json = EXCLUDED.extra_json,
  updated_at = now()
""",
            (name, provider, priority, model, language, api_url, None, resolved_api_key_ref, json.dumps(extra_json, ensure_ascii=False)),
        )
        conn.commit()
        cursor.close()
    finally:
        conn.close()


def sample_rate_for_asr_config(config: ASRConfig) -> int:
    if config.provider == "aliyun_bailian_fun_asr":
        model = normalize_aliyun_fun_asr_model(config.model)
        return 8000 if "-8k-" in model else 16000
    return 16000


def extract_audio(video_path: Path, audio_path: Path, sample_rate: int = 16000) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_command([
        find_ffmpeg(),
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        str(audio_path),
    ])
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "ffmpeg audio extraction failed").strip()
        raise RuntimeError(message)
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError("audio extraction produced an empty file")


def enhance_audio(input_path: Path, output_path: Path, sample_rate: int = 16000) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filters = "highpass=f=80,lowpass=f=7600,afftdn,dynaudnorm,loudnorm,compand=attacks=0.05:decays=0.25:points=-80/-80|-35/-28|-15/-12|0/-3"
    result = run_command([
        find_ffmpeg(),
        "-y",
        "-i",
        str(input_path),
        "-af",
        filters,
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        str(output_path),
    ])
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "ffmpeg audio enhancement failed").strip()
        raise RuntimeError(message)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("audio enhancement produced an empty file")


def cut_audio(input_path: Path, output_path: Path, start: float, duration: float, sample_rate: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_command([
        find_ffmpeg(),
        "-y",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-t",
        f"{max(0.0, duration):.3f}",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        str(output_path),
    ])
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "ffmpeg audio chunk extraction failed").strip()
        raise RuntimeError(message)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"audio chunk extraction produced an empty file: {output_path}")


def prepare_reference_audio(video_path: Path, paths: PipelinePaths, sample_rate: int, speech_preprocess: str) -> dict[str, str]:
    original_path = paths.audio_dir / "original_audio.wav"
    enhanced_path = paths.audio_dir / "asr_enhanced_audio.wav"
    extract_audio(video_path, original_path, sample_rate=sample_rate)
    if speech_preprocess == "ffmpeg_enhance":
        enhance_audio(original_path, enhanced_path, sample_rate=sample_rate)
        shutil.copyfile(enhanced_path, paths.audio_path)
        reference_source = enhanced_path
    elif speech_preprocess == "none":
        shutil.copyfile(original_path, paths.audio_path)
        reference_source = original_path
    else:
        raise ValueError(f"unsupported speech preprocess mode: {speech_preprocess}")
    return {
        "original_audio": str(original_path),
        "enhanced_audio": str(enhanced_path) if enhanced_path.exists() else "",
        "reference_audio": str(paths.audio_path),
        "reference_source": str(reference_source),
    }


def transcribe_local_whisper(audio_path: Path, config: ASRConfig) -> dict[str, Any]:
    try:
        import whisper  # type: ignore
    except Exception as exc:
        raise RuntimeError("Python package 'whisper' is not available. OpenClip Skill initialization must install openai-whisper.") from exc
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
    model = whisper.load_model(config.model)
    kwargs: dict[str, Any] = {"verbose": False}
    if config.language:
        kwargs["language"] = config.language
    result = model.transcribe(str(audio_path), **kwargs)
    return normalize_provider_payload(result, provider=config.provider, model=config.model, language=config.language)


def transcribe_aliyun_bailian_fun_asr(audio_path: Path, config: ASRConfig) -> dict[str, Any]:
    api_key = config.api_key_ciphertext
    if not api_key:
        raise RuntimeError("aliyun_bailian_fun_asr config is missing API key")
    try:
        from dashscope.audio.asr import Recognition  # type: ignore
    except Exception as exc:
        raise RuntimeError("Python package 'dashscope' is not available. OpenClip Skill initialization must install dashscope for Aliyun ASR.") from exc

    model = normalize_aliyun_fun_asr_model(config.model)
    sample_rate = sample_rate_for_asr_config(config)
    recognition = Recognition(model=model, format="wav", sample_rate=sample_rate, callback=None)
    result = recognition.call(str(audio_path), api_key=api_key)
    status_code = getattr(result, "status_code", None)
    if str(status_code) not in {"200", "HTTPStatus.OK"}:
        code = result.get("code", "") if isinstance(result, dict) else getattr(result, "code", "")
        message = result.get("message", "") if isinstance(result, dict) else getattr(result, "message", "")
        raise RuntimeError(f"aliyun_bailian_fun_asr request failed: {status_code} {code} {message}".strip())
    return normalize_dashscope_recognition_payload(result, provider=config.provider, model=model, language=config.language)


def normalize_dashscope_recognition_payload(payload: Any, provider: str, model: str, language: str) -> dict[str, Any]:
    sentences = []
    if hasattr(payload, "get_sentence"):
        sentences = payload.get_sentence() or []
    elif isinstance(payload, dict):
        sentences = ((payload.get("output") or {}).get("sentence") or [])

    segments: list[dict[str, Any]] = []
    texts: list[str] = []
    for item in sentences:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = round(float(item.get("begin_time") or 0) / 1000.0, 3)
        end = round(float(item.get("end_time") or item.get("current_time") or item.get("begin_time") or 0) / 1000.0, 3)
        if end < start:
            end = start
        texts.append(text)
        segments.append({
            "index": len(segments) + 1,
            "start": start,
            "end": end,
            "text": text,
            "source": "asr",
        })

    return {
        "provider": provider,
        "model": model,
        "language": language or "unknown",
        "text": "".join(texts).strip(),
        "segments": segments,
        "raw_sentences": sentences,
    }


def transcribe_audio(audio_path: Path, config: ASRConfig) -> dict[str, Any]:
    if config.provider == "local_whisper":
        return transcribe_local_whisper(audio_path, config)
    if config.provider == "aliyun_bailian_fun_asr":
        return transcribe_aliyun_bailian_fun_asr(audio_path, config)
    raise RuntimeError(f"provider '{config.provider}' is not implemented")


def build_chunks(duration: float, chunk_duration: float, overlap: float, min_chunk_duration: float) -> list[dict[str, Any]]:
    if duration <= 0:
        return []
    chunks: list[dict[str, Any]] = []
    step = max(0.5, chunk_duration - overlap)
    start = 0.0
    while start < duration:
        end = min(duration, start + chunk_duration)
        if end - start >= min_chunk_duration or not chunks:
            chunks.append({"index": len(chunks) + 1, "start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3), "purpose": "primary"})
        if end >= duration:
            break
        start += step
    return chunks


def transcribe_chunk(audio_path: Path, config: ASRConfig, chunk: dict[str, Any], source: str) -> dict[str, Any]:
    chunk_asr = transcribe_audio(audio_path, config)
    offset = float(chunk.get("start") or 0.0)
    segments: list[dict[str, Any]] = []
    for item in chunk_asr.get("segments") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        local_start = max(0.0, float(item.get("start") or 0.0))
        local_end = max(local_start, float(item.get("end") or local_start))
        segments.append({
            "index": 0,
            "start": round(offset + local_start, 3),
            "end": round(offset + local_end, 3),
            "text": text,
            "source": source,
            "chunk_index": int(chunk.get("index") or 0),
            "chunk_start": round(offset, 3),
            "local_start": round(local_start, 3),
            "local_end": round(local_end, 3),
        })
    return {"chunk": chunk, "asr": chunk_asr, "segments": segments}


def segment_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def is_duplicate_segment(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    overlap = min(float(existing["end"]), float(candidate["end"])) - max(float(existing["start"]), float(candidate["start"]))
    if overlap <= 0:
        return False
    shorter = min(float(existing["end"]) - float(existing["start"]), float(candidate["end"]) - float(candidate["start"]))
    if shorter <= 0:
        return False
    similarity = segment_similarity(str(existing.get("text") or ""), str(candidate.get("text") or ""))
    return overlap / shorter >= 0.35 and similarity >= 0.72


def better_segment(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    a_score = len(str(a.get("text") or "")) + 0.5 * max(0.0, float(a.get("end") or 0.0) - float(a.get("start") or 0.0))
    b_score = len(str(b.get("text") or "")) + 0.5 * max(0.0, float(b.get("end") or 0.0) - float(b.get("start") or 0.0))
    return b if b_score > a_score else a


def merge_asr_segments(raw_segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for candidate in sorted(raw_segments, key=lambda item: (float(item.get("start") or 0.0), float(item.get("end") or 0.0))):
        duplicate_index = None
        for index, existing in enumerate(merged):
            if is_duplicate_segment(existing, candidate):
                duplicate_index = index
                break
        if duplicate_index is None:
            merged.append(dict(candidate))
            decisions.append({"action": "keep", "candidate": candidate})
            continue
        existing = merged[duplicate_index]
        chosen = better_segment(existing, candidate)
        merged[duplicate_index] = dict(chosen)
        decisions.append({"action": "dedupe_overlap", "kept": chosen, "dropped": existing if chosen is candidate else candidate, "similarity": round(segment_similarity(str(existing.get("text") or ""), str(candidate.get("text") or "")), 3)})
    for index, item in enumerate(sorted(merged, key=lambda row: (float(row.get("start") or 0.0), float(row.get("end") or 0.0))), start=1):
        item["index"] = index
    return merged, decisions


def run_chunked_asr(reference_audio: Path, paths: PipelinePaths, config: ASRConfig, duration: float, sample_rate: int, args: argparse.Namespace, purpose: str = "primary", base_start: float = 0.0, base_end: float | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    chunk_duration = float(args.chunk_duration if purpose == "primary" else args.gap_recovery_chunk_duration)
    overlap = float(args.chunk_overlap if purpose == "primary" else args.gap_recovery_overlap)
    min_chunk_duration = float(args.min_chunk_duration)
    local_duration = max(0.0, (base_end if base_end is not None else duration) - base_start)
    chunks = build_chunks(local_duration, chunk_duration, overlap, min_chunk_duration)
    chunk_dir = paths.audio_dir / ("chunks" if purpose == "primary" else "recovery_chunks")
    all_segments: list[dict[str, Any]] = []
    chunk_rows: list[dict[str, Any]] = []
    chunk_results: list[dict[str, Any]] = []
    raw_responses: list[dict[str, Any]] = []
    for chunk in chunks:
        global_chunk = dict(chunk)
        global_chunk["start"] = round(base_start + float(chunk["start"]), 3)
        global_chunk["end"] = round(base_start + float(chunk["end"]), 3)
        global_chunk["duration"] = round(float(global_chunk["end"]) - float(global_chunk["start"]), 3)
        global_chunk["purpose"] = purpose
        chunk_path = chunk_dir / f"{purpose}_chunk_{len(chunk_rows) + 1:04d}_t{global_chunk['start']:.3f}_{global_chunk['end']:.3f}.wav"
        cut_audio(reference_audio, chunk_path, float(global_chunk["start"]), float(global_chunk["duration"]), sample_rate)
        global_chunk["audio_path"] = rel_path(chunk_path, paths.workspace)
        chunk_rows.append(global_chunk)
        result = transcribe_chunk(chunk_path, config, global_chunk, f"asr_{purpose}_chunk")
        all_segments.extend(result["segments"])
        raw_sentences = result["asr"].get("raw_sentences") or []
        chunk_results.append({"chunk": global_chunk, "segment_count": len(result["segments"]), "text_chars": len(str(result["asr"].get("text") or "")), "segments": result["segments"]})
        raw_responses.append({"chunk": global_chunk, "provider": config.provider, "model": result["asr"].get("model") or config.model, "raw_sentences": raw_sentences})
    return all_segments, chunk_rows, chunk_results, raw_responses


def normalize_provider_payload(payload: dict[str, Any], provider: str, model: str, language: str) -> dict[str, Any]:
    segments = []
    for index, item in enumerate(payload.get("segments") or [], start=1):
        text = str(item.get("text") or "").strip()
        start = round(float(item.get("start") or 0.0), 3)
        end = round(float(item.get("end") or start), 3)
        if end < start:
            end = start
        segments.append({
            "index": index,
            "start": start,
            "end": end,
            "text": text,
            "source": "asr",
        })
    return {
        "provider": provider,
        "model": model,
        "language": str(payload.get("language") or language or "unknown"),
        "text": str(payload.get("text") or "").strip(),
        "segments": segments,
    }


def compute_silent_ranges(segments: list[dict[str, Any]], duration: float, min_silent_seconds: float = 1.0) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    previous = 0.0
    for item in sorted(segments, key=lambda value: float(value.get("start") or 0.0)):
        start = max(0.0, float(item.get("start") or 0.0))
        end = max(start, float(item.get("end") or start))
        if start - previous >= min_silent_seconds:
            ranges.append({"start": round(previous, 3), "end": round(start, 3), "duration": round(start - previous, 3)})
        previous = max(previous, end)
    if duration - previous >= min_silent_seconds:
        ranges.append({"start": round(previous, 3), "end": round(duration, 3), "duration": round(duration - previous, 3)})
    return ranges


def detect_audio_activity(audio_path: Path, duration: float, noise_db: str = "-35dB", min_silence_seconds: float = 0.5) -> dict[str, Any]:
    result = run_command([
        find_ffmpeg(),
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={noise_db}:d={min_silence_seconds}",
        "-f",
        "null",
        "-",
    ])
    output = (result.stderr or "") + "\n" + (result.stdout or "")
    silences: list[dict[str, Any]] = []
    current_start: float | None = None
    for line in output.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            current_start = float(start_match.group(1))
            continue
        end_match = re.search(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", line)
        if end_match and current_start is not None:
            end = float(end_match.group(1))
            silences.append({"start": round(current_start, 3), "end": round(end, 3), "duration": round(float(end_match.group(2)), 3)})
            current_start = None
    if current_start is not None and duration > current_start:
        silences.append({"start": round(current_start, 3), "end": round(duration, 3), "duration": round(duration - current_start, 3)})

    activity: list[dict[str, Any]] = []
    cursor = 0.0
    for silence in silences:
        start = float(silence["start"])
        if start > cursor:
            activity.append({"start": round(cursor, 3), "end": round(start, 3), "duration": round(start - cursor, 3), "source": "ffmpeg_silencedetect_inverse"})
        cursor = max(cursor, float(silence["end"]))
    if duration > cursor:
        activity.append({"start": round(cursor, 3), "end": round(duration, 3), "duration": round(duration - cursor, 3), "source": "ffmpeg_silencedetect_inverse"})
    return {"noise_db": noise_db, "min_silence_seconds": min_silence_seconds, "silence_ranges": silences, "activity_ranges": activity}


def overlap_seconds(start: float, end: float, ranges: list[dict[str, Any]]) -> float:
    total = 0.0
    for item in ranges:
        total += max(0.0, min(end, float(item.get("end") or 0.0)) - max(start, float(item.get("start") or 0.0)))
    return total


def find_asr_quality_issues(asr: dict[str, Any], duration: float, activity_ranges: list[dict[str, Any]], min_gap: float = 2.5) -> dict[str, Any]:
    gaps = compute_silent_ranges(asr.get("segments") or [], duration, min_silent_seconds=min_gap)
    issues: list[dict[str, Any]] = []
    for gap in gaps:
        start = float(gap["start"])
        end = float(gap["end"])
        active = overlap_seconds(start, end, activity_ranges)
        ratio = active / max(0.001, end - start)
        if ratio >= 0.3:
            issues.append({"type": "asr_gap_with_audio_activity", "start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3), "audio_activity_seconds": round(active, 3), "audio_activity_ratio": round(ratio, 4)})
    return {"items": issues, "asr_gaps": gaps}


def analyze_asr_quality(asr: dict[str, Any], video_duration: float, quality_issues: dict[str, Any] | None = None, audio_activity: dict[str, Any] | None = None) -> dict[str, Any]:
    segments = asr.get("segments") or []
    durations = [max(0.0, float(item.get("end") or 0.0) - float(item.get("start") or 0.0)) for item in segments]
    covered = sum(durations)
    text = str(asr.get("text") or "")
    warnings: list[str] = []
    if not segments:
        warnings.append("ASR returned no segments")
    if not text.strip():
        warnings.append("ASR returned empty text")
    gaps = compute_silent_ranges(segments, video_duration)
    long_gaps = [item for item in gaps if float(item.get("duration") or 0.0) >= 8.0]
    issue_items = (quality_issues or {}).get("items") or []
    if issue_items:
        warnings.append("ASR gaps overlap non-silent audio activity")
    if long_gaps:
        warnings.append("ASR contains long timestamp coverage gaps")
    coverage = covered / video_duration if video_duration > 0 else 0.0
    chars_per_second = len(text) / covered if covered > 0 else 0.0
    if not segments or not text.strip():
        quality = "failed"
    elif len(text) < 12 or coverage < 0.08:
        quality = "weak"
    elif coverage < 0.25 or issue_items:
        quality = "usable"
    elif long_gaps:
        quality = "usable"
    else:
        quality = "good"
    activity_ranges = (audio_activity or {}).get("activity_ranges") or []
    activity_seconds = sum(float(item.get("duration") or 0.0) for item in activity_ranges)
    return {
        "status": "checked",
        "quality_level": quality,
        "provider": asr.get("provider") or "unknown",
        "model": asr.get("model") or "unknown",
        "language": asr.get("language") or "unknown",
        "segment_count": len(segments),
        "text_chars": len(text),
        "covered_speech_seconds": round(covered, 3),
        "video_duration_seconds": round(video_duration, 3),
        "speech_coverage_ratio": round(coverage, 4),
        "avg_segment_seconds": round(covered / len(durations), 3) if durations else 0.0,
        "chars_per_second": round(chars_per_second, 3),
        "silent_ranges": gaps,
        "long_asr_gap_count": len(long_gaps),
        "max_asr_gap_seconds": round(max([float(item.get("duration") or 0.0) for item in gaps] + [0.0]), 3),
        "asr_gap_with_audio_activity_count": len(issue_items),
        "audio_activity_seconds": round(activity_seconds, 3),
        "coverage_vs_audio_activity_ratio": round(covered / activity_seconds, 4) if activity_seconds > 0 else 0.0,
        "timestamp_coverage_suspect": bool(issue_items or long_gaps),
        "warnings": warnings,
    }


def normalize_asr_segments(asr: dict[str, Any], min_segment_chars: int = 4, max_merge_gap_seconds: float = 0.6, mark_long_pause_seconds: float = 1.2) -> dict[str, Any]:
    raw_items = [item for item in (asr.get("segments") or []) if str(item.get("text") or "").strip()]
    normalized: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None

    def finish(item: dict[str, Any] | None) -> None:
        if not item:
            return
        duration = max(0.0, float(item["end"]) - float(item["start"]))
        text = str(item["text"]).strip()
        normalized.append({
            "index": len(normalized) + 1,
            "start": round(float(item["start"]), 3),
            "end": round(float(item["end"]), 3),
            "text": text,
            "source_segment_ids": item["source_segment_ids"],
            "pause_before": 0.0,
            "pause_after": 0.0,
            "char_count": len(text),
            "duration": round(duration, 3),
            "chars_per_second": round(len(text) / duration, 3) if duration > 0 else 0.0,
            "normalization_actions": item["normalization_actions"],
        })

    for raw in raw_items:
        current = {
            "start": float(raw.get("start") or 0.0),
            "end": float(raw.get("end") or 0.0),
            "text": str(raw.get("text") or "").strip(),
            "source_segment_ids": [int(raw.get("index") or len(normalized) + 1)],
            "normalization_actions": [],
        }
        if pending is None:
            pending = current
            continue
        gap = float(current["start"]) - float(pending["end"])
        should_merge = (len(str(pending["text"])) < min_segment_chars or len(str(current["text"])) < min_segment_chars) and gap <= max_merge_gap_seconds
        if should_merge:
            pending["end"] = max(float(pending["end"]), float(current["end"]))
            pending["text"] = f"{pending['text']} {current['text']}".strip()
            pending["source_segment_ids"].extend(current["source_segment_ids"])
            pending["normalization_actions"].append("merged_short_segment")
        else:
            finish(pending)
            pending = current
    finish(pending)

    for index, item in enumerate(normalized):
        previous_end = float(normalized[index - 1]["end"]) if index > 0 else 0.0
        next_start = float(normalized[index + 1]["start"]) if index + 1 < len(normalized) else float(item["end"])
        pause_before = max(0.0, float(item["start"]) - previous_end)
        pause_after = max(0.0, next_start - float(item["end"]))
        item["pause_before"] = round(pause_before, 3)
        item["pause_after"] = round(pause_after, 3)
        if pause_before >= mark_long_pause_seconds:
            item["normalization_actions"].append("long_pause_before")
        if pause_after >= mark_long_pause_seconds:
            item["normalization_actions"].append("long_pause_after")
    return {"items": normalized}


def normalize_timeline_text(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or "")).lower()


def is_sentence_boundary(punctuation: Any) -> bool:
    return bool(re.search(r"[，,。！？!?]", str(punctuation or "")))


def asr_sentence_row(index: int, chunk: dict[str, Any], sentence: dict[str, Any], words: list[dict[str, Any]]) -> dict[str, Any]:
    first = words[0]
    last = words[-1]
    return {
        "index": index,
        "id": f"asr_sentence_{index:03d}",
        "start": round(float(first["start"]), 3),
        "end": round(float(last["end"]), 3),
        "text": "".join(str(word.get("text") or "") + (str(word.get("punctuation") or "") if word is last else "") for word in words).strip(),
        "source_chunk_index": chunk.get("index"),
        "source_chunk_start": round(float(chunk.get("start") or 0.0), 3),
        "source_sentence_id": sentence.get("sentence_id"),
        "source_sentence_text": str(sentence.get("text") or ""),
        "words": words,
        "timing_source": "provider_word_timestamps",
    }


def dedupe_asr_sentence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (float(item.get("start") or 0.0), float(item.get("end") or 0.0))):
        key = normalize_timeline_text(str(row.get("text") or ""))
        if not key:
            continue
        duplicate_index = next((idx for idx, existing in enumerate(output) if normalize_timeline_text(str(existing.get("text") or "")) == key and abs(float(existing.get("start") or 0.0) - float(row.get("start") or 0.0)) <= 0.75), -1)
        if duplicate_index >= 0:
            existing = output[duplicate_index]
            existing_duration = float(existing.get("end") or 0.0) - float(existing.get("start") or 0.0)
            row_duration = float(row.get("end") or 0.0) - float(row.get("start") or 0.0)
            if row_duration > 0 and (existing_duration <= 0 or row_duration < existing_duration):
                output[duplicate_index] = row
            continue
        output.append(row)
    for index, row in enumerate(output, start=1):
        row["index"] = index
        row["id"] = f"asr_sentence_{index:03d}"
    filtered: list[dict[str, Any]] = []
    for row in output:
        if filtered and abs(float(row.get("start") or 0.0) - float(filtered[-1].get("start") or 0.0)) <= 0.05:
            previous = filtered[-1]
            previous_norm = normalize_timeline_text(str(previous.get("text") or ""))
            row_norm = normalize_timeline_text(str(row.get("text") or ""))
            previous_duration = float(previous.get("end") or 0.0) - float(previous.get("start") or 0.0)
            row_duration = float(row.get("end") or 0.0) - float(row.get("start") or 0.0)
            if previous_norm in row_norm or previous_duration <= row_duration:
                filtered[-1] = row
                continue
            if row_norm in previous_norm or row_duration <= previous_duration:
                continue
        filtered.append(row)
    for index, row in enumerate(filtered, start=1):
        row["index"] = index
        row["id"] = f"asr_sentence_{index:03d}"
    return filtered


def build_asr_sentence_timeline(provider_raw_responses: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for chunk_result in provider_raw_responses:
        chunk = chunk_result.get("chunk") or {"index": 1, "start": 0.0}
        chunk_start = float(chunk.get("start") or 0.0)
        for sentence in chunk_result.get("raw_sentences") or []:
            words: list[dict[str, Any]] = []
            for word in sentence.get("words") or []:
                begin_ms = float(word.get("begin_time") or 0.0)
                end_ms = float(word.get("end_time") or word.get("begin_time") or 0.0)
                words.append({
                    "text": str(word.get("text") or ""),
                    "punctuation": str(word.get("punctuation") or ""),
                    "start": round(chunk_start + begin_ms / 1000.0, 3),
                    "end": round(chunk_start + end_ms / 1000.0, 3),
                })
                if is_sentence_boundary(word.get("punctuation")) and words:
                    rows.append(asr_sentence_row(len(rows) + 1, chunk, sentence, words))
                    words = []
            if words:
                rows.append(asr_sentence_row(len(rows) + 1, chunk, sentence, words))
    items = dedupe_asr_sentence_rows(rows)
    return {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "timing_policy": "provider_word_timestamps", "items": items}


def read_video_duration_from_metadata(meta_dir: Path) -> float:
    path = meta_dir / "video_metadata.json"
    if not path.exists():
        return 0.0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return float(payload.get("duration_seconds") or 0.0)
    except Exception:
        return 0.0


def clean_asr_payload(asr: dict[str, Any]) -> dict[str, Any]:
    payload = dict(asr)
    payload.pop("raw_sentences", None)
    for index, item in enumerate(payload.get("segments") or [], start=1):
        if isinstance(item, dict):
            item["index"] = index
    payload["text"] = "".join(str(item.get("text") or "") for item in payload.get("segments") or []).strip() or str(payload.get("text") or "").strip()
    return payload


def run_pipeline(video_path: Path, paths: PipelinePaths, config: ASRConfig, args: argparse.Namespace) -> dict[str, Any]:
    warnings = list(config.warnings)
    video_path = video_path.expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"video file does not exist: {video_path}")
    sample_rate = sample_rate_for_asr_config(config)
    audio_outputs = prepare_reference_audio(video_path, paths, sample_rate=sample_rate, speech_preprocess=args.speech_preprocess)
    video_duration = read_video_duration_from_metadata(paths.meta_dir)
    if video_duration <= 0:
        video_duration = 0.0

    audio_activity = detect_audio_activity(paths.audio_path, video_duration, noise_db=args.audio_activity_noise_db, min_silence_seconds=float(args.audio_activity_min_silence)) if video_duration > 0 else {"silence_ranges": [], "activity_ranges": []}
    write_json(paths.meta_dir / "audio_activity_ranges.json", audio_activity)

    provider_raw_responses: list[dict[str, Any]] = []
    chunk_rows: list[dict[str, Any]] = []
    chunk_results: list[dict[str, Any]] = []
    merge_decisions: list[dict[str, Any]] = []
    recovery_report: dict[str, Any] = {"items": [], "recovered_segment_count": 0}

    if args.asr_strategy == "full_audio":
        raw_asr = transcribe_audio(paths.audio_path, config)
        provider_raw_responses.append({"mode": "full_audio", "provider": config.provider, "model": raw_asr.get("model") or config.model, "raw_sentences": raw_asr.get("raw_sentences") or []})
        asr = clean_asr_payload(raw_asr)
    else:
        primary_segments, primary_chunks, primary_results, primary_raw = run_chunked_asr(paths.audio_path, paths, config, video_duration, sample_rate, args, purpose="primary")
        chunk_rows.extend(primary_chunks)
        chunk_results.extend(primary_results)
        provider_raw_responses.extend(primary_raw)
        merged_segments, merge_decisions = merge_asr_segments(primary_segments)
        asr = {"provider": config.provider, "model": config.model, "language": config.language or "unknown", "text": "".join(str(item.get("text") or "") for item in merged_segments), "segments": merged_segments}

        if args.asr_strategy == "chunked_with_gap_recovery":
            initial_issues = find_asr_quality_issues(asr, video_duration, audio_activity.get("activity_ranges") or [], min_gap=float(args.gap_recovery_min_gap))
            recovered_segments: list[dict[str, Any]] = []
            recovery_items: list[dict[str, Any]] = []
            for issue_index, issue in enumerate(initial_issues.get("items") or [], start=1):
                start = max(0.0, float(issue["start"]) - float(args.gap_recovery_overlap))
                end = min(video_duration, float(issue["end"]) + float(args.gap_recovery_overlap))
                segs, chunks, results, raw = run_chunked_asr(paths.audio_path, paths, config, video_duration, sample_rate, args, purpose="recovery", base_start=start, base_end=end)
                for chunk in chunks:
                    chunk["recovery_issue_index"] = issue_index
                chunk_rows.extend(chunks)
                chunk_results.extend(results)
                provider_raw_responses.extend(raw)
                recovered_segments.extend(segs)
                recovery_items.append({**issue, "recovery_issue_index": issue_index, "recovery_start": round(start, 3), "recovery_end": round(end, 3), "recovered_segment_count": len(segs)})
            if recovered_segments:
                merged_segments, merge_decisions = merge_asr_segments((asr.get("segments") or []) + recovered_segments)
                asr = {"provider": config.provider, "model": config.model, "language": config.language or "unknown", "text": "".join(str(item.get("text") or "") for item in merged_segments), "segments": merged_segments}
            recovery_report = {"items": recovery_items, "recovered_segment_count": len(recovered_segments)}

    if video_duration <= 0:
        video_duration = max([float(item.get("end") or 0.0) for item in asr.get("segments", [])] + [0.0])
        warnings.append("video_metadata.json not found or invalid; ASR duration used for quality analysis")
    quality_issues = find_asr_quality_issues(asr, video_duration, audio_activity.get("activity_ranges") or [], min_gap=float(args.gap_recovery_min_gap))
    quality = analyze_asr_quality(asr, video_duration, quality_issues=quality_issues, audio_activity=audio_activity)
    normalized = normalize_asr_segments(asr)
    asr_sentence_timeline = build_asr_sentence_timeline(provider_raw_responses)

    write_json(paths.meta_dir / "asr_chunks.json", {"items": chunk_rows})
    write_json(paths.meta_dir / "asr_chunk_results.json", {"items": chunk_results})
    write_json(paths.meta_dir / "asr_merge_decisions.json", {"items": merge_decisions})
    write_json(paths.meta_dir / "asr_gap_recovery.json", recovery_report)
    write_json(paths.meta_dir / "asr_quality_issues.json", quality_issues)
    write_json(paths.meta_dir / "asr_provider_raw_responses.json", {"items": provider_raw_responses})
    write_json(paths.meta_dir / "asr_provider_sentences_raw.json", {"items": [{"chunk": item.get("chunk"), "mode": item.get("mode"), "raw_sentences": item.get("raw_sentences") or []} for item in provider_raw_responses]})
    write_json(paths.meta_dir / "asr_sentence_timeline.json", asr_sentence_timeline)
    write_json(paths.transcripts_dir / "transcript.json", asr)
    write_text(paths.transcripts_dir / "original_asr_full.txt", str(asr.get("text") or "").strip() + "\n")
    write_json(paths.meta_dir / "asr_segments.json", asr)
    write_json(paths.meta_dir / "asr_quality.json", quality)
    write_json(paths.meta_dir / "asr_normalized_segments.json", normalized)

    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "provider_config": {
            "name": config.name,
            "provider": config.provider,
            "model": config.model,
            "language": config.language,
            "api_url_configured": bool(config.api_url),
            "api_key_ref": config.api_key_ref,
            "audio_sample_rate": sample_rate,
            "source": config.source,
        },
        "asr_strategy": args.asr_strategy,
        "speech_preprocess": args.speech_preprocess,
        "outputs": {
            "audio": str(paths.audio_path),
            "original_audio": audio_outputs.get("original_audio"),
            "enhanced_audio": audio_outputs.get("enhanced_audio"),
            "transcript": str(paths.transcripts_dir / "transcript.json"),
            "original_asr_full": str(paths.transcripts_dir / "original_asr_full.txt"),
            "asr_segments": str(paths.meta_dir / "asr_segments.json"),
            "asr_quality": str(paths.meta_dir / "asr_quality.json"),
            "normalized_segments": str(paths.meta_dir / "asr_normalized_segments.json"),
            "asr_sentence_timeline": str(paths.meta_dir / "asr_sentence_timeline.json"),
            "asr_chunks": str(paths.meta_dir / "asr_chunks.json"),
            "audio_activity_ranges": str(paths.meta_dir / "audio_activity_ranges.json"),
            "asr_gap_recovery": str(paths.meta_dir / "asr_gap_recovery.json"),
        },
        "counts": {"chunks": len(chunk_rows), "segments": len(asr.get("segments") or []), "asr_sentences": len(asr_sentence_timeline.get("items") or []), "quality_issues": len(quality_issues.get("items") or []), "recovered_segments": recovery_report.get("recovered_segment_count", 0)},
        "quality_level": quality["quality_level"],
        "warnings": warnings + quality.get("warnings", []),
    }
    write_json(paths.meta_dir / "02_audio_asr_pipeline_result.json", result)
    return result


def write_failed_result(paths: PipelinePaths, config: ASRConfig, error_code: str, message: str) -> dict[str, Any]:
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "failed",
        "error_code": error_code,
        "message": message,
        "provider_config": {
            "name": config.name,
            "provider": config.provider,
            "model": config.model,
            "language": config.language,
            "api_url_configured": bool(config.api_url),
            "api_key_ref": config.api_key_ref,
            "audio_sample_rate": sample_rate_for_asr_config(config),
            "source": config.source,
        },
        "warnings": config.warnings,
    }
    write_json(paths.meta_dir / "02_audio_asr_pipeline_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract audio, run ASR, evaluate quality, and normalize ASR segments.")
    parser.add_argument("--video", help="Path to the input video file. Required unless using config management flags.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults to current directory.")
    parser.add_argument("--audio-dir", help="Override audio output directory.")
    parser.add_argument("--meta-dir", help="Override meta output directory.")
    parser.add_argument("--transcripts-dir", help="Override transcripts output directory.")
    parser.add_argument("--audio-output", help="Override reference audio output path.")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV, help="Environment variable containing OpenCrew PostgreSQL URL.")
    parser.add_argument("--config-table", default=DEFAULT_CONFIG_TABLE, help="PostgreSQL ASR provider config table name.")
    parser.add_argument("--config-name", help="Provider config name to load from PostgreSQL.")
    parser.add_argument("--provider", help="Override ASR provider. Supported: local_whisper, aliyun_bailian_fun_asr.")
    parser.add_argument("--model", help="Override ASR model name.")
    parser.add_argument("--language", help="Override ASR language.")
    parser.add_argument("--api-url", help="Override ASR API URL or SDK endpoint marker.")
    parser.add_argument("--asr-strategy", choices=["full_audio", "chunked", "chunked_with_gap_recovery"], default="chunked_with_gap_recovery", help="ASR execution strategy. Default avoids long full-audio recognition drift.")
    parser.add_argument("--speech-preprocess", choices=["none", "ffmpeg_enhance"], default="ffmpeg_enhance", help="Audio preprocessing before ASR. ffmpeg_enhance uses no local model.")
    parser.add_argument("--chunk-duration", type=float, default=20.0, help="Primary ASR chunk duration in seconds.")
    parser.add_argument("--chunk-overlap", type=float, default=2.0, help="Primary ASR chunk overlap in seconds.")
    parser.add_argument("--min-chunk-duration", type=float, default=3.0, help="Minimum chunk duration in seconds.")
    parser.add_argument("--audio-activity-noise-db", default="-35dB", help="ffmpeg silencedetect noise threshold for audio activity checks.")
    parser.add_argument("--audio-activity-min-silence", type=float, default=0.5, help="Minimum silence duration for audio activity checks.")
    parser.add_argument("--gap-recovery-min-gap", type=float, default=2.5, help="Minimum ASR gap duration that can trigger recovery.")
    parser.add_argument("--gap-recovery-chunk-duration", type=float, default=10.0, help="Recovery ASR chunk duration in seconds.")
    parser.add_argument("--gap-recovery-overlap", type=float, default=1.0, help="Recovery ASR chunk overlap and gap padding in seconds.")
    parser.add_argument("--init-config-table", action="store_true", help="Create the PostgreSQL ASR provider config table and exit.")
    parser.add_argument("--upsert-config", action="store_true", help="Upsert an ASR provider config using API key from --api-key-env, then exit.")
    parser.add_argument("--api-key-env", help="Environment variable containing API key for --upsert-config. The key is never printed.")
    parser.add_argument("--api-key-ref", default="", help="Non-secret reference name for the configured API key.")
    parser.add_argument("--allow-empty-api-key", action="store_true", help="Allow creating a cloud provider config without API key for later UI form completion.")
    parser.add_argument("--priority", type=int, default=10, help="Provider priority for --upsert-config.")
    parser.add_argument("--print-json", action="store_true", help="Print pipeline result JSON to stdout.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    paths = resolve_paths(args)
    database_url = os.environ.get(str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL
    if args.init_config_table:
        init_config_table(database_url, args.config_table)
        result = {"tool": TOOL_NAME, "status": "completed", "action": "init_config_table", "table": args.config_table}
        if args.print_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.upsert_config:
        if not args.config_name:
            raise SystemExit("--config-name is required with --upsert-config")
        if not args.provider:
            raise SystemExit("--provider is required with --upsert-config")
        api_key = os.environ.get(str(args.api_key_env or "")) if args.api_key_env else ""
        if args.provider != "local_whisper" and not api_key and not args.allow_empty_api_key:
            raise SystemExit("--api-key-env must point to a populated environment variable for cloud ASR providers")
        upsert_provider_config(
            database_url=database_url,
            table_name=args.config_table,
            name=args.config_name,
            provider=args.provider,
            model=args.model or ("fun-asr-realtime" if args.provider == "aliyun_bailian_fun_asr" else DEFAULT_MODEL),
            language=args.language or DEFAULT_LANGUAGE,
            api_url=args.api_url or (DEFAULT_ALIYUN_FUN_ASR_URL if args.provider == "aliyun_bailian_fun_asr" else ""),
            api_key=api_key,
            api_key_ref=args.api_key_ref,
            priority=args.priority,
            extra_json={"timeout_seconds": 300},
        )
        result = {"tool": TOOL_NAME, "status": "completed", "action": "upsert_config", "config_name": args.config_name, "provider": args.provider, "api_key_stored": bool(api_key)}
        if args.print_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if not args.video:
        raise SystemExit("--video is required unless using --init-config-table or --upsert-config")
    config = resolve_asr_config(args)
    try:
        result = run_pipeline(Path(args.video), paths, config, args)
    except Exception as exc:
        error_code = "pipeline_failed"
        message = str(exc)
        if "whisper" in message.lower():
            error_code = "missing_or_failed_whisper"
        elif "ffmpeg" in message.lower() or "audio" in message.lower():
            error_code = "audio_extraction_failed"
        result = write_failed_result(paths, config, error_code, message)
        if args.print_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} completed: {paths.meta_dir / '02_audio_asr_pipeline_result.json'}")


if __name__ == "__main__":
    main()
