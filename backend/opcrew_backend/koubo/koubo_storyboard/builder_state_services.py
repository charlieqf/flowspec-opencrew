from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from opcrew_backend.adapters.opencode import OpenCodeSessionClient
from opcrew_backend.context import now_ms
from opcrew_backend.routes.media_model_config import CONFIG_TABLE, ensure_table

from .constants import *
from .io_utils import read_json, safe_workspace_rel, write_json
from .runtime import analysis_tool_env
from .text_utils import redact_payload, redact_secret_text


SERVICE_EXPORTS = (
    "safe_upload_name",
    "builder_kind_dir",
    "builder_output_stem",
    "builder_image_suffix",
    "builder_output_name",
    "builder_is_canonical_final_output",
    "builder_is_legacy_final_output",
    "builder_final_output_candidates",
    "latest_final_output_path",
    "final_output_path_for_write",
    "builder_root",
    "legacy_builder_root",
    "builder_config_path",
    "legacy_builder_config_path",
    "builder_section_dir",
    "legacy_builder_section_dir",
    "builder_state_path",
    "legacy_builder_state_path",
    "builder_prompt_path",
    "builder_request_params_path",
    "builder_upload_name",
    "builder_rel",
    "read_builder_config",
    "copy_legacy_builder_file",
    "migrate_legacy_builder_section",
    "resolve_workspace_rel_for_write",
    "reconcile_builder_output",
    "read_builder_section",
    "write_builder_section",
    "read_consistency_guide",
    "builder_reference_summary",
)


def safe_upload_name(filename: str, fallback: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in IMAGE_EXTS:
        suffix = ".png"
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(filename or fallback).stem).strip("_") or fallback
    return f"{stem[:60]}_{now_ms()}_{uuid.uuid4().hex[:6]}{suffix}"


def builder_kind_dir(kind: str) -> str:
    kind = str(kind or "").strip()
    if kind not in {"host", "product"}:
        raise HTTPException(status_code=400, detail="kind must be host or product")
    return kind


def builder_output_stem(kind: str) -> str:
    return "HOST" if builder_kind_dir(kind) == "host" else "Product"


def builder_image_suffix(filename: str, default: str = ".png") -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in IMAGE_EXTS else default


def builder_output_name(kind: str, suffix: str = ".png") -> str:
    normalized_suffix = suffix.lower() if suffix.lower() in IMAGE_EXTS else ".png"
    return f"{builder_output_stem(kind)}{normalized_suffix}"


def builder_is_canonical_final_output(workspace: Path, kind: str, path: Path, *, sc: Any) -> bool:
    return (
        path.parent.resolve() == sc.builder_root(workspace).resolve()
        and path.stem == builder_output_stem(kind)
        and path.suffix.lower() in IMAGE_EXTS
    )


def builder_is_legacy_final_output(kind: str, path: Path) -> bool:
    normalized = builder_kind_dir(kind)
    return (
        path.suffix.lower() in IMAGE_EXTS
        and (
            path.stem == f"{normalized}_create"
            or path.name.startswith(f"{normalized}_upload_output_")
        )
    )


def builder_final_output_candidates(workspace: Path, kind: str, *, sc: Any) -> list[Path]:
    root = sc.builder_root(workspace)
    if not root.exists() or not root.is_dir():
        return []
    return sorted([
        item
        for item in root.iterdir()
        if item.is_file() and (builder_is_canonical_final_output(workspace, kind, item, sc=sc) or builder_is_legacy_final_output(kind, item))
    ])


def latest_final_output_path(workspace: Path, kind: str, *, sc: Any) -> Path | None:
    candidates = builder_final_output_candidates(workspace, kind, sc=sc)
    if not candidates:
        return None
    latest = max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))
    selected = latest
    if not builder_is_canonical_final_output(workspace, kind, latest, sc=sc):
        selected = sc.builder_root(workspace) / builder_output_name(kind, builder_image_suffix(latest.name))
        selected.parent.mkdir(parents=True, exist_ok=True)
        if latest.resolve() != selected.resolve():
            latest.replace(selected)
    for candidate in candidates:
        if candidate.resolve() in {latest.resolve(), selected.resolve()}:
            continue
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
    return selected


def final_output_path_for_write(workspace: Path, kind: str, suffix: str = ".png", *, sc: Any) -> Path:
    target = sc.builder_root(workspace) / builder_output_name(kind, suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    for candidate in builder_final_output_candidates(workspace, kind, sc=sc):
        if candidate.resolve() == target.resolve():
            continue
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
    return target


def builder_root(workspace: Path) -> Path:
    return workspace / "SessionContext" / "Consistency"


def legacy_builder_root(workspace: Path) -> Path:
    return workspace / "consistency_references"


def builder_config_path(workspace: Path) -> Path:
    return builder_root(workspace) / "consistency_config.json"


def legacy_builder_config_path(workspace: Path) -> Path:
    return legacy_builder_root(workspace) / "config.json"


def builder_section_dir(workspace: Path, kind: str) -> Path:
    return builder_root(workspace)


def legacy_builder_section_dir(workspace: Path, kind: str) -> Path:
    return legacy_builder_root(workspace) / builder_kind_dir(kind)


def builder_state_path(workspace: Path, kind: str) -> Path:
    normalized = builder_kind_dir(kind)
    return builder_root(workspace) / f"{normalized}_manifest.json"


def legacy_builder_state_path(workspace: Path, kind: str) -> Path:
    normalized = builder_kind_dir(kind)
    return legacy_builder_section_dir(workspace, normalized) / f"{normalized}_reference_manifest.json"


def builder_prompt_path(workspace: Path, kind: str, name: str) -> Path:
    normalized = builder_kind_dir(kind)
    if name == "simple_prompt.txt":
        filename = f"{normalized}_simple_prompt.txt"
    elif name == "final_prompt.txt":
        filename = f"{normalized}_create_prompt.txt"
    else:
        stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(name).stem).strip("_") or "prompt"
        suffix = Path(name).suffix or ".txt"
        filename = f"{normalized}_{stem}{suffix}"
    return builder_root(workspace) / filename


def builder_request_params_path(workspace: Path, kind: str) -> Path:
    normalized = builder_kind_dir(kind)
    return builder_root(workspace) / f"{normalized}_create_request_{now_ms()}_{uuid.uuid4().hex[:6]}.json"


def builder_upload_name(kind: str, filename: str, role: str, index: int = 1) -> str:
    normalized = builder_kind_dir(kind)
    suffix = Path(filename or "").suffix.lower()
    if suffix not in IMAGE_EXTS:
        suffix = ".png"
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(filename or role).stem).strip("_") or role
    return f"{normalized}_upload_{role}_{index:02d}_{stem[:40]}_{now_ms()}_{uuid.uuid4().hex[:6]}{suffix}"


def builder_rel(workspace: Path, path: Path) -> str:
    return str(path.resolve().relative_to(workspace.resolve()))


def read_builder_config(workspace: Path, *, sc: Any) -> dict[str, Any]:
    config = sc.read_json(builder_config_path(workspace))
    if config:
        return config
    return sc.read_json(legacy_builder_config_path(workspace))


def copy_legacy_builder_file(workspace: Path, rel_path: str, filename: str, *, sc: Any) -> str:
    if not rel_path:
        return ""
    try:
        _rel, source = sc.safe_workspace_rel(workspace, rel_path)
    except HTTPException:
        return ""
    if not source.exists() or not source.is_file():
        return ""
    target = builder_root(workspace) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    return builder_rel(workspace, target)


def migrate_legacy_builder_section(workspace: Path, kind: str, *, sc: Any) -> dict[str, Any]:
    normalized = builder_kind_dir(kind)
    new_path = builder_state_path(workspace, normalized)
    if new_path.exists():
        return sc.read_json(new_path)
    legacy = sc.read_json(legacy_builder_state_path(workspace, normalized))
    if not legacy:
        return {}
    refs: list[str] = []
    for index, rel in enumerate(legacy.get("reference_images") if isinstance(legacy.get("reference_images"), list) else [], start=1):
        source_name = Path(str(rel)).name or f"reference_{index}.png"
        migrated = copy_legacy_builder_file(workspace, str(rel), builder_upload_name(normalized, source_name, "reference", index), sc=sc)
        if migrated:
            refs.append(migrated)
    output_rel = sc.text(legacy.get("output"))
    output_filename = builder_output_name(normalized, builder_image_suffix(output_rel))
    migrated_output = copy_legacy_builder_file(workspace, output_rel, output_filename, sc=sc)
    simple_prompt = sc.text(legacy.get("simple_prompt"))
    final_prompt = sc.text(legacy.get("final_prompt"))
    simple_prompt_path = ""
    final_prompt_path = ""
    if simple_prompt:
        simple_path = builder_prompt_path(workspace, normalized, "simple_prompt.txt")
        simple_path.parent.mkdir(parents=True, exist_ok=True)
        simple_path.write_text(simple_prompt, encoding="utf-8")
        simple_prompt_path = builder_rel(workspace, simple_path)
    if final_prompt:
        final_path = builder_prompt_path(workspace, normalized, "final_prompt.txt")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_text(final_prompt, encoding="utf-8")
        final_prompt_path = builder_rel(workspace, final_path)
    migrated = {
        **legacy,
        "kind": normalized,
        "reference_images": refs,
        "output": migrated_output,
        "output_path": str(workspace / migrated_output) if migrated_output else "",
        "simple_prompt_path": simple_prompt_path or sc.text(legacy.get("simple_prompt_path")),
        "final_prompt_path": final_prompt_path or sc.text(legacy.get("final_prompt_path")),
        "migrated_from": builder_rel(workspace, legacy_builder_state_path(workspace, normalized)),
        "updated_at": now_ms(),
    }
    sc.write_json(new_path, migrated)
    return migrated


def resolve_workspace_rel_for_write(workspace: Path, rel_path: str, *, sc: Any) -> Path:
    rel_value, target = sc.safe_workspace_rel(workspace, rel_path)
    return target


def reconcile_builder_output(workspace: Path, kind: str, manifest: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    normalized = builder_kind_dir(kind)
    output_rel = sc.text(manifest.get("output"))
    latest = latest_final_output_path(workspace, normalized, sc=sc)
    selected: Path | None = latest
    if output_rel:
        try:
            _rel, source = sc.safe_workspace_rel(workspace, output_rel)
        except HTTPException:
            source = None
        if source and source.exists() and source.is_file():
            is_canonical = (
                source.parent.resolve() == builder_root(workspace).resolve()
                and source.stem == builder_output_stem(normalized)
                and source.suffix.lower() in IMAGE_EXTS
            )
            if is_canonical:
                selected = latest or source
            elif not latest or source.stat().st_mtime_ns >= latest.stat().st_mtime_ns:
                target = final_output_path_for_write(workspace, normalized, builder_image_suffix(source.name), sc=sc)
                if source.resolve() != target.resolve():
                    if source.parent.resolve() == builder_root(workspace).resolve():
                        source.replace(target)
                    else:
                        shutil.copy2(source, target)
                selected = target
    next_output = builder_rel(workspace, selected) if selected and selected.exists() else ""
    if next_output == output_rel and sc.text(manifest.get("output_path")) == (str(selected) if selected else ""):
        return manifest
    updated = {
        **manifest,
        "output": next_output,
        "output_path": str(selected) if selected else "",
        "updated_at": now_ms(),
    }
    sc.write_json(builder_state_path(workspace, normalized), updated)
    return updated


def read_builder_section(workspace: Path, kind: str, *, sc: Any) -> dict[str, Any]:
    normalized = builder_kind_dir(kind)
    manifest = sc.read_json(builder_state_path(workspace, normalized)) or migrate_legacy_builder_section(workspace, normalized, sc=sc)
    manifest = reconcile_builder_output(workspace, normalized, manifest, sc=sc)
    output_rel = str(manifest.get("output") or "")
    reference_images = manifest.get("reference_images") if isinstance(manifest.get("reference_images"), list) else []
    return {
        "kind": normalized,
        "simple_prompt": str(manifest.get("simple_prompt") or ""),
        "final_prompt": str(manifest.get("final_prompt") or ""),
        "reference_images": [str(item) for item in reference_images if str(item).strip()],
        "output": output_rel if (workspace / output_rel).exists() else "",
        "manifest": manifest,
    }


def write_builder_section(workspace: Path, kind: str, patch: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    normalized = builder_kind_dir(kind)
    current = read_builder_section(workspace, normalized, sc=sc).get("manifest") or {}
    next_manifest = {**current, **patch, "kind": normalized, "updated_at": now_ms()}
    if "reference_images" not in next_manifest or not isinstance(next_manifest.get("reference_images"), list):
        next_manifest["reference_images"] = []
    sc.write_json(builder_state_path(workspace, normalized), next_manifest)
    return read_builder_section(workspace, normalized, sc=sc)


def read_consistency_guide() -> str:
    if not CONSISTENCY_REFERENCE_GUIDE_PATH.exists():
        return ""
    return CONSISTENCY_REFERENCE_GUIDE_PATH.read_text(encoding="utf-8")[:50000]


def builder_reference_summary(workspace: Path, refs: list[str], *, sc: Any) -> str:
    lines: list[str] = []
    for index, rel in enumerate(refs, start=1):
        try:
            _rel, path = sc.safe_workspace_rel(workspace, rel)
            status = mimetypes.guess_type(path.name)[0] or "image"
            if not path.exists():
                status = "missing"
            lines.append(f"{index}. {rel} ({status})")
        except HTTPException:
            lines.append(f"{index}. {rel} (invalid)")
    return "\n".join(lines) if lines else "(No uploaded reference images.)"


def register_builder_state_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
