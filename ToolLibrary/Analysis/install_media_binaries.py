from __future__ import annotations

import argparse
import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from media_binaries import PROJECT_BIN_DIR, media_dependency_status, media_env


TOOL_NAME = "InstallMediaBinaries"
TOOL_VERSION = "0.1.0"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def chmod_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def copy_binary(src: Path, dst: Path, overwrite: bool) -> dict[str, Any]:
    if dst.exists() and not overwrite:
        chmod_executable(dst)
        return {"path": str(dst), "source": str(src), "copied": False, "size_bytes": dst.stat().st_size}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    chmod_executable(dst)
    return {"path": str(dst), "source": str(src), "copied": True, "size_bytes": dst.stat().st_size}


def version(path: Path) -> str:
    result = subprocess.run([str(path), "-version"], capture_output=True, text=True, check=False, env=media_env())
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0] if text else ""


def install_from_static_ffmpeg(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from static_ffmpeg.run import get_platform_key, get_or_fetch_platform_executables_else_raise  # type: ignore
    except Exception as exc:
        raise RuntimeError("Python package static-ffmpeg is required. Install with: python3 -m pip install --user static-ffmpeg") from exc

    cache_dir = PROJECT_BIN_DIR.parent / "vendor" / "static_ffmpeg" / get_platform_key()
    ffmpeg_src, ffprobe_src = get_or_fetch_platform_executables_else_raise(download_dir=str(cache_dir))
    PROJECT_BIN_DIR.mkdir(parents=True, exist_ok=True)
    ffmpeg = copy_binary(Path(ffmpeg_src), PROJECT_BIN_DIR / "ffmpeg", bool(args.overwrite))
    ffprobe = copy_binary(Path(ffprobe_src), PROJECT_BIN_DIR / "ffprobe", bool(args.overwrite))
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "source": "static-ffmpeg",
        "project_bin_dir": str(PROJECT_BIN_DIR),
        "cache_dir": str(cache_dir),
        "outputs": {"ffmpeg": ffmpeg, "ffprobe": ffprobe},
        "versions": {"ffmpeg": version(Path(ffmpeg["path"])), "ffprobe": version(Path(ffprobe["path"]))},
        "dependency_status": media_dependency_status(),
    }
    if args.result_path:
        write_json(Path(args.result_path).expanduser().resolve(), result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install project-local ffmpeg and ffprobe into OpenCrew/.bin.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing OpenCrew/.bin/ffmpeg and ffprobe.")
    parser.add_argument("--result-path", help="Optional JSON result path.")
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = install_from_static_ffmpeg(args)
    except Exception as exc:
        result = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "failed", "message": str(exc), "dependency_status": media_dependency_status()}
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("status") == "completed":
        print(f"Installed ffmpeg/ffprobe into {PROJECT_BIN_DIR}")
    else:
        print(f"{TOOL_NAME} failed: {result.get('message')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
