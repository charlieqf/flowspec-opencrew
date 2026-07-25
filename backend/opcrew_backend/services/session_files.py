from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException


SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "secrets.json",
    "secret.json",
    "token.json",
    "tokens.json",
    "credentials.json",
    "credential.json",
    "auth.json",
    "cookies.txt",
}

SENSITIVE_PARTS = {
    "secret",
    "secrets",
    "token",
    "tokens",
    "credential",
    "credentials",
    "cookie",
    "cookies",
    "debug_raw",
    "raw_debug",
}

PROVIDER_SIDECAR_ROOTS = {
    "sessionoutput/storyboard/assets/images",
    "sessionoutput/storyboard/assets/audios",
    "sessionoutput/storyboard/assets/videos",
    "sessionscratch/cleanimagegenerations",
}

PROVIDER_MANIFEST_PATHS = {
    "sessionoutput/storyboard/koubo_storyboard_assets.json",
}

INTERNAL_WORKSPACE_ROOTS = {
    "sessioncontext",
}

TOOL_WORKING_ROOT_RE = re.compile(r"^s\d+(?:_\d+)+_[a-z0-9]")

INTERNAL_EXECUTION_FILE_SUFFIXES = (
    "_execution_state.json",
    "_execution_result.json",
)


def clean_relative_path(relative_path: str) -> str:
    cleaned = relative_path.replace("\\", "/").strip().lstrip("/")
    path = Path(cleaned)
    if not cleaned or path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail="Invalid file path")
    return path.as_posix()


def is_hidden_path(relative_path: str) -> bool:
    return any(part.startswith(".") for part in Path(relative_path).parts)


def is_provider_sidecar_path(relative_path: str) -> bool:
    normalized = "/".join(part.lower() for part in Path(relative_path.replace("\\", "/")).parts)
    if normalized in PROVIDER_MANIFEST_PATHS:
        return True
    if not normalized.endswith(".json"):
        return False
    return any(normalized.startswith(f"{root}/") for root in PROVIDER_SIDECAR_ROOTS)


def is_sensitive_path(relative_path: str) -> bool:
    parts = [part.lower() for part in Path(relative_path).parts]
    name = parts[-1] if parts else ""
    root = parts[0] if parts else ""
    if root in INTERNAL_WORKSPACE_ROOTS or TOOL_WORKING_ROOT_RE.match(root):
        return True
    if name.endswith(INTERNAL_EXECUTION_FILE_SUFFIXES):
        return True
    if name in SENSITIVE_NAMES:
        return True
    if is_provider_sidecar_path(relative_path):
        return True
    stem = Path(name).stem.lower()
    return any(part in SENSITIVE_PARTS for part in parts) or stem in SENSITIVE_PARTS


def default_file_visibility(relative_path: str) -> tuple[str, str, int]:
    if is_hidden_path(relative_path) or is_sensitive_path(relative_path):
        return "internal", "sensitive", 0
    if relative_path.startswith("meta/") or relative_path.startswith("history/"):
        return "internal", "normal", 0
    return "public", "normal", 1


class SessionFileService:
    def resolve_workspace_path(self, root: Path, relative_path: str, *, require_file: bool = True) -> tuple[str, Path]:
        rel = clean_relative_path(relative_path)
        root_resolved = root.resolve()
        candidate = root / rel
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="File path escapes workspace") from exc
        if require_file and (not resolved.exists() or not resolved.is_file()):
            raise HTTPException(status_code=404, detail="File not found")
        if is_hidden_path(rel) or is_sensitive_path(rel):
            raise HTTPException(status_code=403, detail="File is not downloadable")
        return rel, resolved

    def classify(self, relative_path: str) -> dict[str, Any]:
        visibility, sensitivity, downloadable = default_file_visibility(relative_path)
        return {"visibility": visibility, "sensitivity": sensitivity, "downloadable": downloadable}

    def visible_file_rows(self, rows: list[dict[str, Any]], *, audience: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in rows:
            path = str(row.get("path") or "")
            policy = self.classify(path)
            visibility = str(row.get("visibility") or policy["visibility"])
            sensitivity = str(row.get("sensitivity") or policy["sensitivity"])
            downloadable = int(row.get("downloadable") if row.get("downloadable") is not None else policy["downloadable"])
            if is_hidden_path(path) or is_sensitive_path(path) or sensitivity == "sensitive":
                continue
            if audience == "share" and (visibility != "public" or downloadable != 1):
                continue
            items.append({**row, "visibility": visibility, "sensitivity": sensitivity, "downloadable": downloadable})
        return items

    def resolve_download(
        self,
        root: Path,
        relative_path: str,
        *,
        row: dict[str, Any] | None = None,
        audience: str = "customer",
    ) -> Path:
        rel, resolved = self.resolve_workspace_path(root, relative_path, require_file=True)
        policy = self.classify(rel)
        visibility = str((row or {}).get("visibility") or policy["visibility"])
        sensitivity = str((row or {}).get("sensitivity") or policy["sensitivity"])
        downloadable = int((row or {}).get("downloadable") if (row or {}).get("downloadable") is not None else policy["downloadable"])
        if sensitivity == "sensitive" or downloadable != 1:
            raise HTTPException(status_code=403, detail="File is not downloadable")
        if audience == "share" and visibility != "public":
            raise HTTPException(status_code=403, detail="File is not available for this share")
        return resolved

    def zip_entries(self, root: Path, current: Path, *, audience: str = "customer") -> list[tuple[Path, str]]:
        root_resolved = root.resolve()
        current_resolved = current.resolve()
        try:
            current_resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Zip path escapes workspace") from exc
        paths = [current_resolved] if current_resolved.is_file() else [item for item in current_resolved.rglob("*") if item.is_file()]
        entries: list[tuple[Path, str]] = []
        for item in paths:
            try:
                rel = item.resolve().relative_to(root_resolved).as_posix()
            except ValueError:
                continue
            if is_hidden_path(rel) or is_sensitive_path(rel):
                continue
            visibility, sensitivity, downloadable = default_file_visibility(rel)
            if sensitivity == "sensitive" or downloadable != 1:
                continue
            if audience == "share" and visibility != "public":
                continue
            arcname = item.name if current_resolved.is_file() else item.relative_to(current_resolved).as_posix()
            entries.append((item.resolve(), arcname))
        return entries
