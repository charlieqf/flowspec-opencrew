from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


class WorkspacePathError(ValueError):
    pass


def clean_relative_path(relative_path: str) -> str:
    cleaned = relative_path.replace("\\", "/").strip().lstrip("/")
    path = Path(cleaned)
    if not cleaned or path.is_absolute() or ".." in path.parts:
        raise WorkspacePathError("Invalid workspace-relative path")
    return path.as_posix()


def resolve_workspace_file(root: Path, relative_path: str, *, require_file: bool = True) -> tuple[str, Path]:
    rel = clean_relative_path(relative_path)
    root_resolved = root.resolve()
    candidate = root / rel
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise WorkspacePathError("Path escapes workspace") from exc
    if require_file and (not resolved.exists() or not resolved.is_file()):
        raise WorkspacePathError("Workspace file not found")
    return rel, resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_workspace_file(root: Path, relative_path: str, dest: Path) -> tuple[str, int]:
    _, source = resolve_workspace_file(root, relative_path, require_file=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return sha256_file(dest), dest.stat().st_size
