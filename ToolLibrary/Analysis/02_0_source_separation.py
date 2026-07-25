from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from media_binaries import find_ffmpeg, media_dependency_status, media_env


TOOL_NAME = "SourceSeparation"
TOOL_VERSION = "0.1.0"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def command_env() -> dict[str, str]:
    return media_env()


def run_command(args: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout, env=command_env())


def command_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in [result.stderr, result.stdout] if part).strip()


def resolve_paths(args: argparse.Namespace) -> dict[str, Path]:
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
    audio_dir = Path(args.audio_dir).expanduser().resolve() if args.audio_dir else workspace / "audio"
    meta_dir = Path(args.meta_dir).expanduser().resolve() if args.meta_dir else workspace / "meta"
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else audio_dir / "separated"
    return {"workspace": workspace, "audio_dir": audio_dir, "meta_dir": meta_dir, "output_dir": output_dir}


def source_path(args: argparse.Namespace) -> Path:
    raw = args.audio or args.video
    if not raw:
        raise ValueError("Either --video or --audio is required")
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"source file does not exist: {path}")
    return path


def extract_audio(input_path: Path, output_path: Path, sample_rate: int) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_command([
        find_ffmpeg(),
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        str(sample_rate),
        str(output_path),
    ])
    if result.returncode != 0:
        raise RuntimeError(command_output(result) or "audio extraction failed")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("audio extraction produced an empty file")
    return {"path": str(output_path), "size_bytes": output_path.stat().st_size, "sample_rate": sample_rate}


def demucs_command(args: argparse.Namespace, input_audio: Path, demucs_output: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "demucs",
        "--two-stems",
        "vocals",
        "-n",
        str(args.model),
        "-o",
        str(demucs_output),
    ]
    if args.device:
        command.extend(["--device", str(args.device)])
    if args.shifts is not None:
        command.extend(["--shifts", str(int(args.shifts))])
    if args.segment is not None:
        command.extend(["--segment", str(float(args.segment))])
    command.append(str(input_audio))
    return command


def find_demucs_track(demucs_output: Path, stem: str) -> Path:
    matches = sorted(demucs_output.glob(f"**/{stem}.wav"))
    if not matches:
        raise RuntimeError(f"demucs did not produce {stem}.wav under {demucs_output}")
    return matches[0]


def copy_track(src: Path, dst: Path, overwrite: bool) -> dict[str, Any]:
    if dst.exists() and not overwrite:
        return {"path": str(dst), "size_bytes": dst.stat().st_size, "copied": False, "reason": "already_exists"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return {"path": str(dst), "size_bytes": dst.stat().st_size, "copied": True, "reason": "copied"}


def audio_probe(path: Path) -> dict[str, Any]:
    result = run_command([find_ffmpeg(), "-hide_banner", "-i", str(path)])
    output = result.stderr or result.stdout or ""
    return {"path": str(path), "size_bytes": path.stat().st_size if path.exists() else 0, "ffmpeg_info": output[-4000:]}


def run_separation(args: argparse.Namespace) -> dict[str, Any]:
    paths = resolve_paths(args)
    src = source_path(args)
    audio_dir = paths["audio_dir"]
    meta_dir = paths["meta_dir"]
    output_dir = paths["output_dir"]
    original_audio = Path(args.audio).expanduser().resolve() if args.audio else audio_dir / "source_separation_input.wav"

    extraction: dict[str, Any]
    if args.audio:
        extraction = {"path": str(original_audio), "size_bytes": original_audio.stat().st_size, "sample_rate": None, "source": "audio_input"}
    else:
        extraction = extract_audio(src, original_audio, int(args.sample_rate))
        extraction["source"] = "video_input"

    output_dir.mkdir(parents=True, exist_ok=True)
    vocals_path = output_dir / "vocals.wav"
    no_vocals_path = output_dir / "no_vocals.wav"

    with tempfile.TemporaryDirectory(prefix="opencrew_demucs_") as tmp:
        demucs_output = Path(tmp) / "demucs"
        command = demucs_command(args, original_audio, demucs_output)
        result = run_command(command, timeout=int(args.timeout_seconds))
        if result.returncode != 0:
            raise RuntimeError(command_output(result) or "demucs source separation failed")
        demucs_vocals = find_demucs_track(demucs_output, "vocals")
        demucs_no_vocals = find_demucs_track(demucs_output, "no_vocals")
        vocals = copy_track(demucs_vocals, vocals_path, bool(args.overwrite))
        no_vocals = copy_track(demucs_no_vocals, no_vocals_path, bool(args.overwrite))
        archive_dir: Path | None = None
        if bool(args.keep_demucs_output):
            archive_dir = output_dir / "demucs_raw"
            if archive_dir.exists() and bool(args.overwrite):
                shutil.rmtree(archive_dir)
            if not archive_dir.exists():
                shutil.copytree(demucs_output, archive_dir)

    result_payload = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "engine": "demucs",
        "model": str(args.model),
        "workspace": str(paths["workspace"]),
        "source": str(src),
        "input_audio": extraction,
        "media_dependencies": media_dependency_status(),
        "outputs": {
            "vocals": vocals,
            "no_vocals": no_vocals,
            "source_separation": str(meta_dir / "source_separation.json"),
            "demucs_raw": str(archive_dir) if archive_dir is not None else "",
        },
        "probes": {
            "vocals": audio_probe(vocals_path),
            "no_vocals": audio_probe(no_vocals_path),
        },
        "next_step_hint": "Use audio/separated/vocals.wav as the ASR/VAD input when background music is too strong.",
    }
    write_json(meta_dir / "source_separation.json", result_payload)
    return result_payload


def failed_result(args: argparse.Namespace, message: str) -> dict[str, Any]:
    paths = resolve_paths(args)
    lower = message.lower()
    if "no module named demucs" in lower or "demucs" in lower and "module" in lower:
        error_code = "missing_demucs"
    elif "ffmpeg" in lower or "audio" in lower:
        error_code = "audio_extraction_failed"
    else:
        error_code = "source_separation_failed"
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "failed",
        "engine": "demucs",
        "model": str(args.model),
        "workspace": str(paths["workspace"]),
        "error_code": error_code,
        "message": message,
        "required_dependencies": ["project ffmpeg and ffprobe", "python package demucs"],
        "media_dependencies": media_dependency_status(),
    }
    write_json(paths["meta_dir"] / "source_separation.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Separate vocals from background music for ASR/VAD preprocessing.")
    parser.add_argument("--video", help="Input video path. Either --video or --audio is required.")
    parser.add_argument("--audio", help="Input audio path. Skips video audio extraction when provided.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/audio and <workspace>/meta.")
    parser.add_argument("--audio-dir", help="Override audio output directory. Defaults to <workspace>/audio.")
    parser.add_argument("--meta-dir", help="Override meta output directory. Defaults to <workspace>/meta.")
    parser.add_argument("--output-dir", help="Override separated output directory. Defaults to <workspace>/audio/separated.")
    parser.add_argument("--model", default="htdemucs", help="Demucs model name. Default: htdemucs.")
    parser.add_argument("--device", default="", help="Optional demucs device, e.g. cpu, cuda, mps.")
    parser.add_argument("--sample-rate", type=int, default=44100, help="Sample rate for extracted source audio.")
    parser.add_argument("--shifts", type=int, default=0, help="Demucs shifts. 0 is fastest; higher can improve quality.")
    parser.add_argument("--segment", type=float, help="Optional demucs segment length in seconds.")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing vocals/no_vocals outputs.")
    parser.add_argument("--keep-demucs-output", action="store_true", help="Keep raw demucs output tree under audio/separated/demucs_raw.")
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = run_separation(args)
    except Exception as exc:
        result = failed_result(args, str(exc))
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("status") == "completed":
        print(f"{TOOL_NAME} completed: {result['outputs']['vocals']['path']}")
    else:
        print(f"{TOOL_NAME} failed: {result.get('message')}")


if __name__ == "__main__":
    main()
