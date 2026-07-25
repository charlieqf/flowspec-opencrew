#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import wave
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_CATALOG_REL = f"ToolLibrary/Analysis_V1/VoiceCatalog/{DEFAULT_MODEL}"
DEFAULT_WORKDIR = "/private/tmp/opencrew-analysis-v1-voice-catalog"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
SAMPLE_TEXT_ID = "fixed_cn_v1"
SAMPLE_DURATION = 16.0


def resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_quick_module(repo_root: Path) -> Any:
    module_path = repo_root / "ToolLibrary" / "Analysis_V1" / "03_02_TTSBuilderQuick.py"
    spec = importlib.util.spec_from_file_location("analysis_v1_tts_builder_quick_asset_helper", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load helper module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_voice_filter(raw: str) -> set[str]:
    return {item.strip() for item in str(raw or "").split(",") if item.strip()}


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)


def catalog_audio_rel(item: dict[str, Any]) -> str:
    audio = item.get("audio") if isinstance(item.get("audio"), dict) else {}
    return str(item.get("sample_audio_path") or audio.get("path") or "").strip()


def catalog_audio_path(catalog_dir: Path, item: dict[str, Any]) -> Path:
    rel = catalog_audio_rel(item)
    if not rel:
        voice = str(item.get("voice") or item.get("voice_id") or "").strip()
        rel = f"{voice}_{SAMPLE_TEXT_ID}_16s.wav"
    return catalog_dir / rel


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wav_metadata(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as reader:
        return {
            "path": path.name,
            "duration": round(reader.getnframes() / float(reader.getframerate()), 3),
            "sample_rate": reader.getframerate(),
            "channels": reader.getnchannels(),
            "sample_width_bytes": reader.getsampwidth(),
            "frames": reader.getnframes(),
            "format": "wav",
            "sha256": sha256_file(path),
        }


def build_sample_prompt(sample_text: str) -> str:
    return (
        "请用自然、清楚的普通话朗读以下固定声音测试文本。"
        "只朗读正文，不要读出任何说明、标题或标点名称；"
        "语速保持自然，不要播音腔、广告腔或夸张表演。\n\n"
        f"正文：\n{sample_text.strip()}"
    ).strip()


def trim_sample(module: Any, raw_audio: Path, output_audio: Path, duration: float) -> dict[str, Any]:
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    raw_duration = module.media_duration(raw_audio) or duration
    filters = (
        f"aresample=48000,aformat=channel_layouts=stereo,"
        f"atrim=duration={duration:.6f},apad=pad_dur={duration:.6f},"
        f"atrim=duration={duration:.6f},asetpts=N/SR/TB"
    )
    module.run_cmd(
        [
            module.find_binary("ffmpeg"),
            "-y",
            "-i",
            str(raw_audio),
            "-af",
            filters,
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output_audio),
        ],
        timeout=180,
    )
    return {"raw_duration": round(float(raw_duration), 3), **wav_metadata(output_audio)}


def selected_items(payload: dict[str, Any], voices_filter: set[str]) -> list[dict[str, Any]]:
    voices = payload.get("voices")
    if not isinstance(voices, list):
        raise RuntimeError("voice_catalog_index.json has no voices list.")
    selected = []
    for item in voices:
        if not isinstance(item, dict):
            continue
        voice = str(item.get("voice") or item.get("voice_id") or "").strip()
        if not voice:
            continue
        if voices_filter and voice not in voices_filter:
            continue
        selected.append(item)
    if voices_filter:
        found = {str(item.get("voice") or item.get("voice_id") or "").strip() for item in selected}
        missing = sorted(voices_filter - found)
        if missing:
            raise RuntimeError(f"Requested voices are not in catalog: {', '.join(missing)}")
    return selected


def validate_audio(catalog_dir: Path, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures = []
    for item in items:
        voice = str(item.get("voice") or item.get("voice_id") or "").strip()
        audio_path = catalog_audio_path(catalog_dir, item)
        if not audio_path.exists():
            failures.append({"voice": voice, "path": str(audio_path), "reason": "missing"})
            continue
        expected_sha = str((item.get("audio") if isinstance(item.get("audio"), dict) else {}).get("sha256") or "").strip()
        if expected_sha and sha256_file(audio_path) != expected_sha:
            failures.append({"voice": voice, "path": str(audio_path), "reason": "sha256_mismatch"})
    return failures


def update_summary(catalog_dir: Path, item: dict[str, Any], audio_meta: dict[str, Any]) -> None:
    summary_path = catalog_dir / f"{SAMPLE_TEXT_ID}_generation_summary.json"
    if not summary_path.exists():
        return
    summary = read_json(summary_path)
    voices = summary.get("voices")
    if not isinstance(voices, list):
        return
    voice = str(item.get("voice") or item.get("voice_id") or "").strip()
    for row in voices:
        if not isinstance(row, dict) or str(row.get("voice") or "").strip() != voice:
            continue
        row["raw_duration"] = audio_meta["raw_duration"]
        row["duration"] = audio_meta["duration"]
        row["path"] = audio_meta["path"]
        break
    write_json(summary_path, summary)


def load_api_key(module: Any, args: argparse.Namespace, provider: str, model: str, workdir: Path) -> str:
    helper_args = module.Args(
        workspace=str(workdir),
        voice_catalog_dir=str(args.catalog_dir),
        provider=provider,
        model=model,
        voices=args.voices,
        reference_start=0.0,
        reference_duration=SAMPLE_DURATION,
        final_count=3,
        database_url=args.database_url,
        database_url_env=args.database_url_env,
        force=args.force,
        resume=False,
        print_json=False,
    )
    return str(module.load_tts_api_key(helper_args, provider, model)).strip()


def generate_missing(module: Any, args: argparse.Namespace, catalog_dir: Path, payload: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    provider = str(payload.get("provider") or "google").strip().lower()
    model = str(payload.get("model") or DEFAULT_MODEL).strip()
    sample_text_id = str(payload.get("sample_text_id") or "").strip()
    if sample_text_id != SAMPLE_TEXT_ID:
        raise RuntimeError(f"Unsupported sample_text_id={sample_text_id}; expected {SAMPLE_TEXT_ID}.")
    sample_text = str(payload.get("sample_text") or "").strip()
    if not sample_text:
        raise RuntimeError("voice_catalog_index.json has no sample_text.")
    workdir = Path(args.workdir).expanduser().resolve()
    prompt_dir = workdir / "prompts"
    raw_dir = workdir / "raw"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    api_key = load_api_key(module, args, provider, model, workdir)
    if not api_key:
        raise RuntimeError(f"No enabled Google/Gemini TTS API key found for model={model}.")
    prompt_text = build_sample_prompt(sample_text)
    generated = []
    for item in items:
        voice = str(item.get("voice") or item.get("voice_id") or "").strip()
        output_audio = catalog_audio_path(catalog_dir, item)
        if output_audio.exists() and not args.force:
            continue
        rel = output_audio.name
        prompt_path = prompt_dir / f"{safe_name(voice)}_{SAMPLE_TEXT_ID}.txt"
        raw_audio = raw_dir / f"{safe_name(voice)}_{SAMPLE_TEXT_ID}_raw.wav"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        module.call_gemini_tts(
            api_key,
            model,
            voice,
            prompt_path,
            raw_audio,
            workspace=workdir,
            asset_key=f"voice_catalog_{safe_name(voice)}_{SAMPLE_TEXT_ID}",
        )
        audio_meta = trim_sample(module, raw_audio, output_audio, SAMPLE_DURATION)
        audio = dict(item.get("audio") if isinstance(item.get("audio"), dict) else {})
        audio.update({key: value for key, value in audio_meta.items() if key != "raw_duration"})
        audio["path"] = rel
        item["sample_audio_path"] = rel
        item["raw_duration"] = audio_meta["raw_duration"]
        item["audio"] = audio
        update_summary(catalog_dir, item, {**audio_meta, "path": rel})
        generated.append({"voice": voice, "path": str(output_audio), "sha256": audio["sha256"]})
    if generated:
        write_json(catalog_dir / "voice_catalog_index.json", payload)
    return generated


def main() -> int:
    repo_root = resolve_repo_root()
    parser = argparse.ArgumentParser(description="Generate or verify Analysis_V1 system VoiceCatalog wav assets.")
    parser.add_argument("--catalog-dir", default=str(repo_root / DEFAULT_CATALOG_REL))
    parser.add_argument("--voices", default="", help="Comma-separated voices to generate/check. Defaults to every catalog voice.")
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--force", action="store_true", help="Regenerate existing wav files and update catalog metadata.")
    parser.add_argument("--check-only", action="store_true", help="Only verify wav existence and sha256 metadata.")
    parser.add_argument("--clean-workdir", action="store_true", help="Remove the temporary workdir before generation.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args()

    catalog_dir = Path(args.catalog_dir).expanduser().resolve()
    index_path = catalog_dir / "voice_catalog_index.json"
    if not index_path.exists():
        raise RuntimeError(f"Missing catalog index: {index_path}")
    if args.clean_workdir and not args.check_only:
        workdir = Path(args.workdir).expanduser().resolve()
        if workdir.exists():
            shutil.rmtree(workdir)
    payload = read_json(index_path)
    items = selected_items(payload, parse_voice_filter(args.voices))
    failures_before = validate_audio(catalog_dir, items)
    generated: list[dict[str, Any]] = []
    if not args.check_only:
        missing_items = [
            item
            for item in items
            if args.force or not catalog_audio_path(catalog_dir, item).exists()
        ]
        if missing_items:
            module = load_quick_module(repo_root)
            generated = generate_missing(module, args, catalog_dir, payload, missing_items)
        failures_after = validate_audio(catalog_dir, items)
    else:
        failures_after = failures_before
    output = {
        "catalog_dir": str(catalog_dir),
        "checked": len(items),
        "generated": generated,
        "failures": failures_after,
    }
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"checked={len(items)} generated={len(generated)} failures={len(failures_after)}")
        for failure in failures_after:
            print(f"{failure['reason']}: {failure['voice']} {failure['path']}")
    return 1 if failures_after else 0


if __name__ == "__main__":
    raise SystemExit(main())
