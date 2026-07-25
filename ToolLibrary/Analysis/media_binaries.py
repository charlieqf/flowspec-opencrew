from __future__ import annotations

import os
import shutil
from pathlib import Path


OPENCREW_ROOT = Path(__file__).resolve().parents[1]
PROJECT_BIN_DIR = OPENCREW_ROOT / ".bin"


def project_binary(name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return PROJECT_BIN_DIR / f"{name}{suffix}"


def _env_path(name: str) -> str:
    if name == "ffmpeg":
        return os.environ.get("OPENCREW_FFMPEG_PATH", "")
    if name == "ffprobe":
        return os.environ.get("OPENCREW_FFPROBE_PATH", "")
    return ""


def find_binary(name: str, *, allow_imageio_ffmpeg: bool = False) -> str:
    configured = _env_path(name)
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.exists():
            return str(path)
        raise RuntimeError(f"{name} configured by environment does not exist: {path}")

    bundled = project_binary(name)
    if bundled.exists():
        return str(bundled)

    path = shutil.which(name)
    if path:
        return path

    if allow_imageio_ffmpeg and name == "ffmpeg":
        try:
            import imageio_ffmpeg  # type: ignore

            return str(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception as exc:
            raise RuntimeError("ffmpeg is not available and imageio_ffmpeg fallback failed") from exc

    raise RuntimeError(f"{name} is not available. Install project media binaries into {PROJECT_BIN_DIR}")


def find_ffmpeg() -> str:
    return find_binary("ffmpeg", allow_imageio_ffmpeg=True)


def find_ffprobe() -> str:
    return find_binary("ffprobe", allow_imageio_ffmpeg=False)


def media_path_dirs() -> list[str]:
    dirs: list[str] = []
    if PROJECT_BIN_DIR.exists():
        dirs.append(str(PROJECT_BIN_DIR))
    for binary in ("ffmpeg", "ffprobe"):
        try:
            directory = str(Path(find_binary(binary, allow_imageio_ffmpeg=binary == "ffmpeg")).parent)
        except Exception:
            continue
        if directory not in dirs:
            dirs.append(directory)
    return dirs


def media_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    dirs = media_path_dirs()
    if dirs:
        env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
    if extra:
        env.update(extra)
    return env


def media_dependency_status() -> dict[str, object]:
    items = {}
    for name in ("ffmpeg", "ffprobe"):
        try:
            path = find_binary(name, allow_imageio_ffmpeg=name == "ffmpeg")
            items[name] = {"available": True, "path": path, "project_bundled": Path(path).resolve() == project_binary(name).resolve()}
        except Exception as exc:
            items[name] = {"available": False, "path": "", "project_bundled": False, "error": str(exc)}
    return {"project_bin_dir": str(PROJECT_BIN_DIR), "items": items}
