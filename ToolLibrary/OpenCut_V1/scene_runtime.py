from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any


VARIABLES_REL = "SessionContext/Variables.json"
DEFAULT_SOURCE_VIDEO_REL = "SessionContext/Video_Source.mp4"
DEFAULT_METADATA_REL = "SessionContext/Video_Metadata.json"


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def relpath(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def resolve_workspace(raw: str) -> Path:
    return (Path(raw).expanduser() if raw else Path.cwd()).resolve()


def load_inputs(workspace: Path) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    if not workspace.is_dir():
        raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
    variables_path = workspace / VARIABLES_REL
    if not variables_path.is_file():
        raise BlockedError("variables_missing", f"Required context is missing: {VARIABLES_REL}")
    variables = read_json(variables_path)
    source_rel = str(variables.get("source_video_path") or DEFAULT_SOURCE_VIDEO_REL).strip()
    source = Path(source_rel)
    if source.is_absolute():
        raise BlockedError("source_video_path_not_relative", "source_video_path must be workspace-relative.")
    source = workspace / source
    if not source.is_file():
        raise BlockedError("source_video_missing", f"Source video is missing: {source_rel}")
    metadata_rel = str(variables.get("video_metadata_path") or DEFAULT_METADATA_REL).strip()
    metadata_path = Path(metadata_rel)
    if metadata_path.is_absolute():
        raise BlockedError("video_metadata_path_not_relative", "video_metadata_path must be workspace-relative.")
    metadata_path = workspace / metadata_path
    if not metadata_path.is_file():
        raise BlockedError("video_metadata_missing", f"Video metadata is missing: {metadata_rel}")
    metadata = read_json(metadata_path)
    return variables, source, metadata


def source_fingerprint(source: Path) -> str:
    stat = source.stat()
    raw = f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def base_result(tool: str, version: str, workspace: Path, *, force: bool, resume: bool) -> dict[str, Any]:
    return {
        "tool": tool,
        "tool_version": version,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": False,
        "inputs": {},
        "outputs": {},
        "created_files": [],
        "cleanup_actions": [],
        "warnings": [],
        "blocked_reasons": [],
        "force": force,
        "resume": resume,
        "updated_at": now_iso(),
    }


def finish_error(result: dict[str, Any], exc: Exception) -> None:
    if isinstance(exc, BlockedError):
        result["status"] = "blocked"
        result["blocked_reasons"].append({"code": exc.code, "message": exc.message})
    else:
        result["status"] = "failed"
        result["warnings"].append({"code": "unexpected_error", "message": str(exc)})
    result["updated_at"] = now_iso()
