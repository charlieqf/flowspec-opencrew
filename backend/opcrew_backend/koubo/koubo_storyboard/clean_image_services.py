from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from opcrew_backend.context import now_ms
from opcrew_backend.services.media_sanitize import sanitize_image_file, write_sanitized_image_bytes

from .constants import *
from .io_utils import read_json, safe_workspace_rel, write_json
from .usage_metering import image_usage_units, record_storyboard_usage


CLEAN_IMAGE_SCHEMA = "clean_single_image_generation_0.1"
DEFAULT_CLEAN_IMAGE_SIZE = "1536x1024"


SERVICE_EXPORTS = (
    "clean_image_generation_id",
    "clean_image_root",
    "clean_image_reference_root",
    "clean_image_generation_dir",
    "clean_image_manifest_path",
    "clean_image_manifest",
    "clean_image_output_path",
    "clean_image_output_rel",
    "clean_image_reference_filename",
    "clean_image_reference_path",
    "clean_image_reference_rel",
    "save_clean_image_reference_bytes",
    "clean_image_effective_prompt",
    "clean_image_reference_paths",
    "clean_image_payload",
    "list_clean_image_generations",
    "update_clean_image_manifest_promotion",
    "clean_image_asset_payload",
    "generate_clean_image",
    "clean_image_task_payload",
    "promote_clean_image_to_asset_library",
    "clean_dialogue_bound_image_path",
    "promote_clean_image_to_dialogue",
    "promote_clean_image_to_consistency",
)


def clean_image_generation_id() -> str:
    return f"cln_{now_ms()}_{uuid.uuid4().hex[:8]}"


def clean_image_root(workspace: Path) -> Path:
    return workspace / CLEAN_IMAGE_REL


def clean_image_reference_root(workspace: Path) -> Path:
    return workspace / CLEAN_IMAGE_REFERENCES_REL


def clean_image_generation_dir(workspace: Path, generation_id: str, *, sc: Any) -> Path:
    generation_id = sc.text(generation_id)
    if not generation_id.startswith("cln_") or "/" in generation_id or "\\" in generation_id or ".." in generation_id:
        raise HTTPException(status_code=400, detail="Invalid clean image generation id")
    rel, path = sc.safe_workspace_rel(workspace, f"{CLEAN_IMAGE_REL}/{generation_id}")
    if not rel.startswith(f"{CLEAN_IMAGE_REL}/"):
        raise HTTPException(status_code=400, detail="Clean image path must stay inside clean image scratch")
    return path


def clean_image_manifest_path(workspace: Path, generation_id: str, *, sc: Any) -> Path:
    return clean_image_generation_dir(workspace, generation_id, sc=sc) / "manifest.json"


def clean_image_manifest(workspace: Path, generation_id: str, *, sc: Any) -> dict[str, Any]:
    manifest = sc.read_json(clean_image_manifest_path(workspace, generation_id, sc=sc))
    if not manifest:
        raise HTTPException(status_code=404, detail="Clean image generation not found")
    return manifest


def clean_image_output_path(workspace: Path, generation_id: str, *, sc: Any) -> Path:
    manifest = clean_image_manifest(workspace, generation_id, sc=sc)
    output_rel = sc.text(manifest.get("output_path"))
    if not output_rel:
        raise HTTPException(status_code=404, detail="Clean image output is missing")
    rel, path = sc.safe_workspace_rel(workspace, output_rel)
    if not rel.startswith(f"{CLEAN_IMAGE_REL}/{generation_id}/"):
        raise HTTPException(status_code=400, detail="Clean image output must stay inside its scratch directory")
    if not path.exists() or not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(status_code=404, detail="Clean image output file not found")
    return path


def clean_image_output_rel(workspace: Path, generation_id: str, *, sc: Any) -> str:
    return clean_image_output_path(workspace, generation_id, sc=sc).relative_to(workspace).as_posix()


def clean_image_reference_filename(filename: str, index: int = 1, content_type: str = "", *, sc: Any) -> str:
    fallback = f"reference_{index:03d}.png"
    name = sc.safe_name(filename or "", fallback)
    while ".." in name:
        name = name.replace("..", ".")
    name = name.strip(" .") or fallback
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_EXTS:
        guessed = mimetypes.guess_extension(content_type or "") or ".png"
        suffix = guessed.lower()
        if suffix == ".jpe":
            suffix = ".jpg"
        if suffix not in IMAGE_EXTS:
            raise HTTPException(status_code=400, detail="Reference image must be a supported image file")
        name = f"{Path(name).stem or Path(fallback).stem}{suffix}"
    return f"{now_ms()}_{index:03d}_{uuid.uuid4().hex[:8]}_{name}"


def clean_image_reference_path(workspace: Path, reference_id: str, *, sc: Any) -> Path:
    reference_id = sc.text(reference_id)
    if not reference_id or "/" in reference_id or "\\" in reference_id or ".." in reference_id:
        raise HTTPException(status_code=400, detail="Invalid clean image reference id")
    rel, path = sc.safe_workspace_rel(workspace, f"{CLEAN_IMAGE_REFERENCES_REL}/{reference_id}")
    if not rel.startswith(f"{CLEAN_IMAGE_REFERENCES_REL}/"):
        raise HTTPException(status_code=400, detail="Clean image reference must stay inside reference scratch")
    if not path.exists() or not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(status_code=404, detail="Clean image reference image not found")
    return path


def clean_image_reference_rel(workspace: Path, path: Path) -> str:
    rel = path.relative_to(workspace).as_posix()
    if not rel.startswith(f"{CLEAN_IMAGE_REFERENCES_REL}/"):
        raise HTTPException(status_code=400, detail="Clean image reference must stay inside reference scratch")
    return rel


def save_clean_image_reference_bytes(workspace: Path, filename: str, content: bytes, index: int = 1, content_type: str = "", *, sc: Any) -> dict[str, Any]:
    if not content:
        raise HTTPException(status_code=400, detail="Reference image is empty")
    target_dir = clean_image_reference_root(workspace)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / clean_image_reference_filename(filename, index, content_type, sc=sc)
    target.write_bytes(content)
    rel = clean_image_reference_rel(workspace, target)
    return {
        "path": rel,
        "filename": target.name,
        "original_filename": filename or target.name,
        "content_type": content_type or mimetypes.guess_type(target.name)[0] or "image/png",
    }


def clean_image_effective_prompt(prompt: str, negative_prompt: str = "", *, sc: Any) -> str:
    prompt = sc.text(prompt)
    negative = sc.text(negative_prompt)
    if not negative:
        return prompt
    return f"{prompt.rstrip()}\n\nNegative: {negative.strip()}"


def clean_image_reference_paths(workspace: Path, values: Any, *, sc: Any) -> tuple[list[str], list[Path]]:
    refs: list[str] = []
    paths: list[Path] = []
    seen: set[str] = set()
    iterable = values if isinstance(values, list) else []
    for item in iterable:
        rel = sc.text(item)
        if not rel or rel in seen:
            continue
        seen.add(rel)
        if len(refs) >= 8:
            raise HTTPException(status_code=400, detail={"message": "Clean image supports at most 8 reference images", "limit": 8})
        safe_rel, path = sc.safe_workspace_rel(workspace, rel)
        if not path.exists() or not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
            raise HTTPException(status_code=400, detail={"message": "Selected reference image was not found", "path": rel})
        refs.append(safe_rel)
        paths.append(path)
    return refs, paths


def clean_image_payload(manifest: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    generation_id = sc.text(manifest.get("generation_id"))
    return {
        "generation_id": generation_id,
        "created_at": int(manifest.get("created_at") or 0),
        "provider": sc.text(manifest.get("provider")),
        "model": sc.text(manifest.get("model")),
        "requested_size": sc.text(manifest.get("requested_size")),
        "effective_size": sc.text(manifest.get("effective_size")),
        "prompt": sc.text(manifest.get("prompt")),
        "negative_prompt": sc.text(manifest.get("negative_prompt")),
        "reference_paths": [sc.text(item) for item in manifest.get("reference_paths") or [] if sc.text(item)],
        "output_path": sc.text(manifest.get("output_path")),
        "manifest_path": sc.text(manifest.get("manifest_path")),
        "promotions": manifest.get("promotions") if isinstance(manifest.get("promotions"), list) else [],
    }


def list_clean_image_generations(workspace: Path, *, sc: Any) -> list[dict[str, Any]]:
    root = clean_image_root(workspace)
    if not root.exists() or not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name, reverse=True):
        if not path.is_dir():
            continue
        manifest = sc.read_json(path / "manifest.json")
        if manifest.get("schema_version") == CLEAN_IMAGE_SCHEMA:
            items.append(clean_image_payload(manifest, sc=sc))
    return items


def update_clean_image_manifest_promotion(workspace: Path, generation_id: str, promotion: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    path = clean_image_manifest_path(workspace, generation_id, sc=sc)
    manifest = sc.read_json(path)
    if not manifest:
        raise HTTPException(status_code=404, detail="Clean image generation not found")
    promotions = manifest.get("promotions") if isinstance(manifest.get("promotions"), list) else []
    promotions.append({**promotion, "created_at": now_ms()})
    manifest["promotions"] = promotions
    manifest["updated_at"] = now_ms()
    sc.write_json(path, manifest)
    return manifest


def clean_image_asset_payload(rel_path: str, label: str, origin: dict[str, Any]) -> dict[str, Any]:
    filename = Path(rel_path).name
    return {
        "id": rel_path,
        "path": rel_path,
        "label": label or filename,
        "filename": filename,
        "asset_type": "Image",
        "kind": "image",
        "source": "clean_generated",
        "created_at": now_ms(),
        "origin": origin,
    }


def generate_clean_image(task: dict[str, Any], payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    prompt = sc.text(payload.get("prompt"))
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    negative_prompt = sc.text(payload.get("negative_prompt"))
    effective_prompt = clean_image_effective_prompt(prompt, negative_prompt, sc=sc)
    requested_size = sc.text(payload.get("size") or payload.get("effective_size"))
    effective_size = requested_size or DEFAULT_CLEAN_IMAGE_SIZE
    refs, reference_paths = clean_image_reference_paths(workspace, payload.get("reference_paths") or payload.get("reference_images") or [], sc=sc)
    if reference_paths:
        config, reference_image_provider_fallback_from = sc.load_reference_image_config(sc.text(payload.get("provider")), sc.text(payload.get("model")), sc=sc)
    else:
        config = sc.load_image_config(sc.text(payload.get("provider")), sc.text(payload.get("model")), sc=sc)
        reference_image_provider_fallback_from = ""
    generation_id = clean_image_generation_id()
    generation_dir = clean_image_generation_dir(workspace, generation_id, sc=sc)
    output_path = generation_dir / "image.png"
    output_rel = output_path.relative_to(workspace).as_posix()
    manifest_rel = (generation_dir / "manifest.json").relative_to(workspace).as_posix()
    image_bytes = sc.generate_image_bytes(config, effective_prompt, reference_paths or None, effective_size, sc=sc)
    generation_dir.mkdir(parents=True, exist_ok=True)
    write_sanitized_image_bytes(output_path, image_bytes)
    local_usage = record_storyboard_usage(
        sc.ctx,
        task,
        request_id=generation_id,
        provider=config["provider"],
        model_id=config["model"],
        modality="image",
        step_id="koubo_storyboard.clean_image",
        units=image_usage_units(count=1, prompt=effective_prompt, reference_count=len(reference_paths)),
    )
    manifest = {
        "schema_version": CLEAN_IMAGE_SCHEMA,
        "kind": "clean_single_image_generation",
        "generation_id": generation_id,
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "created_at": now_ms(),
        "provider": config["provider"],
        "model": config["model"],
        "requested_size": requested_size,
        "effective_size": effective_size,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "effective_prompt": effective_prompt,
        "effective_prompt_sha256": hashlib.sha256(effective_prompt.encode("utf-8")).hexdigest(),
        "reference_paths": refs,
        "reference_count": len(reference_paths),
        "reference_image_provider_fallback_from": reference_image_provider_fallback_from,
        "output_path": output_rel,
        "manifest_path": manifest_rel,
        "local_usage": local_usage,
        "local_usage_id": local_usage.get("local_usage_id", ""),
        "promotions": [],
    }
    sc.write_json(generation_dir / "manifest.json", manifest)
    return clean_image_payload(manifest, sc=sc)


def clean_image_task_payload(task: dict[str, Any], plan_override: Any = None, *, sc: Any) -> dict[str, Any]:
    plan, meta = sc.load_plan(task, sc=sc)
    if isinstance(plan_override, dict) and isinstance(plan_override.get("shots"), list):
        plan = plan_override
    return {"task": task, "meta": meta, "plan": plan}


def promote_clean_image_to_asset_library(task: dict[str, Any], generation_id: str, payload: dict[str, Any] | None = None, *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    payload = payload if isinstance(payload, dict) else {}
    manifest = clean_image_manifest(workspace, generation_id, sc=sc)
    source = clean_image_output_path(workspace, generation_id, sc=sc)
    output_dir = workspace / ASSET_IMAGES_REL
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower() if source.suffix.lower() in IMAGE_EXTS else ".png"
    output_name = f"{now_ms()}_001_clean_generated_{uuid.uuid4().hex[:8]}{suffix}"
    output_path = output_dir / output_name
    sanitize_image_file(source, output_path)
    output_rel = f"{ASSET_IMAGES_REL}/{output_name}"
    sidecar_rel = f"{ASSET_IMAGES_REL}/{Path(output_name).stem}.json"
    sidecar = {
        "request_id": f"koubo_clean_image_{generation_id}",
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "generation_id": generation_id,
        "provider": sc.text(manifest.get("provider")),
        "model": sc.text(manifest.get("model")),
        "size": sc.text(manifest.get("effective_size")),
        "reference_images": manifest.get("reference_paths") if isinstance(manifest.get("reference_paths"), list) else [],
        "reference_count": int(manifest.get("reference_count") or 0),
        "output": output_rel,
        "prompt": sc.text(manifest.get("prompt")),
        "negative_prompt": sc.text(manifest.get("negative_prompt")),
        "effective_prompt": sc.text(manifest.get("effective_prompt")),
        "source": "clean_generated",
        "generated_at": now_ms(),
    }
    sc.write_json(workspace / sidecar_rel, sidecar)
    asset = clean_image_asset_payload(output_rel, "Clean generated image", {
        "tool": "clean_single_image_generation",
        "generation_id": generation_id,
        "prompt": sc.text(manifest.get("prompt")),
        "negative_prompt": sc.text(manifest.get("negative_prompt")),
        "provider": sc.text(manifest.get("provider")),
        "model": sc.text(manifest.get("model")),
        "reference_images": sidecar["reference_images"],
        "request_path": sidecar_rel,
    })
    sc.upsert_asset_manifest_item(workspace, asset, sc=sc)
    updated_manifest = update_clean_image_manifest_promotion(workspace, generation_id, {
        "target": "asset_library",
        "target_path": output_rel,
        "request_path": sidecar_rel,
        "asset_id": asset["id"],
    }, sc=sc)
    return {"asset": asset, "generation": clean_image_payload(updated_manifest, sc=sc), **clean_image_task_payload(task, payload.get("plan"), sc=sc)}


def clean_dialogue_bound_image_path(plan: dict[str, Any], dialogue_id: str, *, sc: Any) -> str:
    _shot, _scene, dialogue = sc.find_dialogue(plan, dialogue_id, sc=sc)
    assets = sc.ensure_dialogue_working_assets(dialogue, sc=sc)
    images = assets.get("images") if isinstance(assets.get("images"), list) else []
    return sc.text((images[0] or {}).get("path")) if images else sc.text(dialogue.get("bound_image_path"))


def promote_clean_image_to_dialogue(task: dict[str, Any], generation_id: str, payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    source_rel = clean_image_output_rel(workspace, generation_id, sc=sc)
    dialogue_id = sc.text(payload.get("dialogue_id"))
    if not dialogue_id:
        raise HTTPException(status_code=400, detail="dialogue_id is required")
    plan, regroup_backup = sc.coerce_edit_plan(task, workspace, payload.get("plan"), bool(payload.get("regroup_working_assets") or payload.get("clear_working_on_regroup")), sc=sc)
    plan = sc.bind_asset_to_plan(workspace, plan, dialogue_id, source_rel, "image", sc=sc)
    sc.save_edit_and_source_storyboard(task, workspace, sc.recalculate(plan, sc=sc), sc=sc)
    working_path = clean_dialogue_bound_image_path(plan, dialogue_id, sc=sc)
    updated_manifest = update_clean_image_manifest_promotion(workspace, generation_id, {
        "target": "dialogue_image",
        "dialogue_id": dialogue_id,
        "source_path": source_rel,
        "working_path": working_path,
        "regroup_backup": regroup_backup,
    }, sc=sc)
    return {"generation": clean_image_payload(updated_manifest, sc=sc), "dialogue_id": dialogue_id, "working_path": working_path, **clean_image_task_payload(task, sc=sc)}


def promote_clean_image_to_consistency(task: dict[str, Any], generation_id: str, payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    kind = sc.builder_kind_dir(sc.text(payload.get("kind")))
    source = clean_image_output_path(workspace, generation_id, sc=sc)
    previous = sc.read_builder_section(workspace, kind, sc=sc)
    previous_manifest = previous.get("manifest") if isinstance(previous.get("manifest"), dict) else {}
    suffix = source.suffix.lower() if source.suffix.lower() in IMAGE_EXTS else ".png"
    target = sc.final_output_path_for_write(workspace, kind, suffix, sc=sc)
    sanitize_image_file(source, target)
    output_rel = sc.builder_rel(workspace, target)
    section = sc.write_builder_section(workspace, kind, {
        "output": output_rel,
        "output_path": output_rel,
        "source_type": "clean_generated",
        "clean_generation_id": generation_id,
        "previous_output": sc.text(previous.get("output")),
        "previous_output_origin": previous_manifest.get("origin") or {
            "source_type": sc.text(previous_manifest.get("source_type")),
            "clean_generation_id": sc.text(previous_manifest.get("clean_generation_id")),
        },
    }, sc=sc)
    updated_manifest = update_clean_image_manifest_promotion(workspace, generation_id, {
        "target": "consistency",
        "kind": kind,
        "target_path": output_rel,
    }, sc=sc)
    return {"kind": kind, "section": section, "generation": clean_image_payload(updated_manifest, sc=sc)}


def register_clean_image_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
