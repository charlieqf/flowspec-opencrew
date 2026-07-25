from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .errors import MediaClipError
from .models import CLIP_ID_RE, clean_display_name


SAFE_COMPONENT_RE = re.compile(r"[^0-9A-Za-z._\-\u0080-\uffff]+")


def safe_clip_filename(display_name: str) -> str:
    name = clean_display_name(display_name)
    name = unicodedata.normalize("NFKC", name)
    name = SAFE_COMPONENT_RE.sub("_", name).strip(" ._-")
    name = name[:120].strip(" ._-") or "clip"
    return f"{name}.mp4"


def _controlled_relative_path(value: str) -> Path:
    relative = Path(str(value or ""))
    if (
        not relative.as_posix()
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise MediaClipError(
            "media_clip_path_invalid",
            "派生片段路径无效。",
            status_code=409,
        )
    return relative


def _assert_no_symlink_components(root: Path, relative: Path) -> None:
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise MediaClipError(
                "media_clip_path_invalid",
                "派生片段路径包含不安全的符号链接。",
                status_code=409,
            )


def resolve_controlled_path(
    workspace: Path,
    relative_path: str,
    *,
    must_exist: bool = False,
) -> Path:
    root = workspace.expanduser().resolve()
    relative = _controlled_relative_path(relative_path)
    _assert_no_symlink_components(root, relative)
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise MediaClipError(
            "media_clip_path_invalid",
            "派生片段路径越出素材工作区。",
            status_code=409,
        )
    if must_exist and (not target.is_file() or target.is_symlink()):
        raise MediaClipError(
            "media_clip_file_missing",
            "派生片段文件不存在或不安全。",
            status_code=409,
        )
    return target


@dataclass(frozen=True)
class ClipPaths:
    clip_id: str
    relative_path: str
    directory: Path
    part_path: Path
    final_path: Path


@dataclass(frozen=True)
class OrphanCleanupReport:
    removed_parts: tuple[str, ...]
    removed_orphans: tuple[str, ...]
    missing_registered: tuple[str, ...]
    removed_directories: tuple[str, ...]


class ClipStorage:
    def __init__(self, workspace: Path) -> None:
        root = workspace.expanduser()
        if not root.is_absolute():
            raise MediaClipError(
                "media_clip_workspace_invalid",
                "素材工作区路径无效。",
                status_code=409,
            )
        self.workspace = root.resolve()

    def source_file(self, relative_path: str) -> Path:
        return resolve_controlled_path(
            self.workspace, relative_path, must_exist=True
        )

    def allocate(self, clip_id: str, display_name: str) -> ClipPaths:
        if not CLIP_ID_RE.fullmatch(str(clip_id or "")):
            raise MediaClipError(
                "media_clip_id_invalid",
                "派生片段 ID 无效。",
                status_code=409,
            )
        filename = safe_clip_filename(display_name)
        relative = Path("SessionOutput") / "clips" / clip_id / filename
        final_path = resolve_controlled_path(
            self.workspace, relative.as_posix()
        )
        directory = final_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_components(
            self.workspace, directory.relative_to(self.workspace)
        )
        if directory.resolve() != directory or not directory.is_relative_to(
            self.workspace
        ):
            raise MediaClipError(
                "media_clip_path_invalid",
                "派生片段输出目录不安全。",
                status_code=409,
            )
        part_path = directory / (
            f".{filename}.{uuid.uuid4().hex}.part.mp4"
        )
        if (
            part_path.exists()
            or part_path.is_symlink()
            or final_path.exists()
            or final_path.is_symlink()
        ):
            raise MediaClipError(
                "media_clip_output_exists",
                "派生片段输出路径已存在。",
                status_code=409,
            )
        return ClipPaths(
            clip_id=clip_id,
            relative_path=relative.as_posix(),
            directory=directory,
            part_path=part_path,
            final_path=final_path,
        )

    @staticmethod
    def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def cleanup_paths(paths: ClipPaths, *, include_final: bool = False) -> None:
        paths.part_path.unlink(missing_ok=True)
        if include_final:
            paths.final_path.unlink(missing_ok=True)
        try:
            paths.directory.rmdir()
        except OSError:
            pass

    def delete_file_transactionally(
        self,
        *,
        relative_path: str,
        delete_database: Callable[[], Any],
    ) -> None:
        final_path = resolve_controlled_path(self.workspace, relative_path)
        tombstone: Path | None = None
        if final_path.exists():
            if not final_path.is_file() or final_path.is_symlink():
                raise MediaClipError(
                    "media_clip_path_invalid",
                    "派生片段文件路径不安全。",
                    status_code=409,
                )
            tombstone = final_path.with_name(
                f".{final_path.name}.{uuid.uuid4().hex}.deleting"
            )
            os.replace(final_path, tombstone)
        try:
            delete_database()
        except Exception:
            if tombstone is not None and tombstone.exists():
                os.replace(tombstone, final_path)
            raise
        if tombstone is not None:
            tombstone.unlink(missing_ok=True)
        try:
            final_path.parent.rmdir()
        except OSError:
            pass

    def cleanup_orphans(
        self,
        *,
        registered_output_paths: Iterable[str],
        now_ms: int,
        ttl_ms: int,
    ) -> OrphanCleanupReport:
        registered = {str(item) for item in registered_output_paths}
        removed_parts: list[str] = []
        removed_orphans: list[str] = []
        missing_registered: list[str] = []
        removed_directories: list[str] = []
        for relative in sorted(registered):
            try:
                path = resolve_controlled_path(self.workspace, relative)
            except MediaClipError:
                missing_registered.append(relative)
                continue
            if not path.is_file():
                missing_registered.append(relative)
        root = resolve_controlled_path(
            self.workspace, "SessionOutput/clips"
        )
        if not root.is_dir():
            return OrphanCleanupReport(
                tuple(removed_parts),
                tuple(removed_orphans),
                tuple(missing_registered),
                tuple(removed_directories),
            )
        threshold_seconds = max(0, int(now_ms) - max(0, int(ttl_ms))) / 1000
        for directory in sorted(root.iterdir()):
            if (
                not directory.is_dir()
                or directory.is_symlink()
                or not CLIP_ID_RE.fullmatch(directory.name)
            ):
                continue
            for path in sorted(directory.iterdir()):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(self.workspace).as_posix()
                if relative in registered:
                    continue
                try:
                    old_enough = path.stat().st_mtime <= threshold_seconds
                except OSError:
                    continue
                if not old_enough:
                    continue
                if ".part." in path.name or path.name.endswith(".part"):
                    path.unlink(missing_ok=True)
                    removed_parts.append(relative)
                elif path.suffix.lower() == ".mp4":
                    path.unlink(missing_ok=True)
                    removed_orphans.append(relative)
            try:
                directory.rmdir()
                removed_directories.append(
                    directory.relative_to(self.workspace).as_posix()
                )
            except OSError:
                pass
        try:
            root.rmdir()
        except OSError:
            pass
        return OrphanCleanupReport(
            tuple(removed_parts),
            tuple(removed_orphans),
            tuple(missing_registered),
            tuple(removed_directories),
        )


__all__ = [
    "ClipPaths",
    "ClipStorage",
    "OrphanCleanupReport",
    "resolve_controlled_path",
    "safe_clip_filename",
]
