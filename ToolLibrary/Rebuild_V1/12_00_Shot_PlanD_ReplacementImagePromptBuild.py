from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


TOOL_ID = "12_00_Shot_PlanD_ReplacementImagePromptBuild"
TOOL_NAME = "Shot Final Image TTS Video Prompt Build"
TOOL_VERSION = "1.0.0"
DEFAULT_VARIANT_ID = "variant_001"
GUIDE_NAME = "提示词撰写指南_口播_人物产品一致性模型_GPT.MD"
PROMPT_PACKAGE_VERSION = "final_v1"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"


class BlockedError(RuntimeError):
    def __init__(self, errors: list[str], dependencies: dict[str, Any] | None = None) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors
        self.dependencies = dependencies or {}


class ToolError(RuntimeError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_scene_prompt_tool() -> Any:
    path = Path(__file__).with_name("05_01_Scene_ScenePromptRefresh.py")
    spec = importlib.util.spec_from_file_location("rebuild_v1_05_01_scene_for_plan_d", path)
    if not spec or not spec.loader:
        raise ToolError(f"Unable to load OpenCode helper tool: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "")) or "item"


def rel(workspace: Path, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(str(path))
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def workspace_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.exists() or not path.is_dir():
        raise ToolError(f"Workspace directory not found: {path}")
    return path.resolve()


def strip_srt_timing(text: str) -> str:
    lines: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def shot_id_of(shot: dict[str, Any]) -> str:
    return str(shot.get("shot_id") or shot.get("id") or "").strip()


def scene_id_of(mark: dict[str, Any]) -> str:
    return str(mark.get("scene_mark_id") or mark.get("scene_id") or mark.get("id") or "").strip()


def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    marks = reference.get("scene_marks") if isinstance(reference.get("scene_marks"), list) else []
    return [item for item in marks if isinstance(item, dict)]


def find_shot(plan: dict[str, Any], shot_id: str) -> dict[str, Any]:
    shot = next((item for item in plan.get("shots") or [] if isinstance(item, dict) and shot_id_of(item) == shot_id), None)
    if not shot:
        raise ToolError(f"Shot not found in rebuild_shot_plan.json: {shot_id}")
    return shot


def scene_text(shot: dict[str, Any], mark: dict[str, Any]) -> str:
    for key in ("srt_text", "scene_srt", "text", "subtitle"):
        value = strip_srt_timing(str(mark.get(key) or ""))
        if value:
            return value
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    for key in ("srt_text", "summary", "video_prompt", "prompt"):
        value = strip_srt_timing(str(desc.get(key) or ""))
        if value:
            return value
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    return strip_srt_timing(str(reference.get("srt_text") or shot.get("srt_text") or ""))


def scene_visual(mark: dict[str, Any]) -> str:
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    for key in ("video_prompt", "motion_prompt", "visual_prompt", "prompt", "summary"):
        value = str(desc.get(key) or "").strip()
        if value:
            return value
    return ""


def shot_tts_text(shot: dict[str, Any]) -> str:
    parts = [scene_text(shot, mark) for mark in scene_marks_for_shot(shot)]
    text_value = " ".join(item for item in parts if item).strip()
    if text_value:
        return text_value
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    for key in ("srt_text", "spoken_script", "script"):
        value = strip_srt_timing(str(reference.get(key) or shot.get(key) or ""))
        if value:
            return value
    return ""


def tts_selection(shot: dict[str, Any]) -> dict[str, Any]:
    value = shot.get("tts_selection") if isinstance(shot.get("tts_selection"), dict) else {}
    return value


def extract_speed_notes(selection: dict[str, Any]) -> list[str]:
    blob = " ".join(str(selection.get(key) or "") for key in ("prompt", "prompt_template", "instructions", "voice_style"))
    notes: list[str] = []
    for pattern in (r"语速[^;；。,.，]*", r"停顿[^;；。,.，]*", r"节奏[^;；。,.，]*", r"speed[^;；。,.，]*", r"pace[^;；。,.，]*"):
        notes.extend(match.strip() for match in re.findall(pattern, blob, flags=re.I) if match.strip())
    return list(dict.fromkeys(notes)) or ["语速更快一点", "停顿更短", "保持自然生活短视频口播节奏"]


def build_tts_prompt(shot: dict[str, Any]) -> tuple[str, list[str]]:
    text_value = shot_tts_text(shot)
    selection = tts_selection(shot)
    speed_notes = extract_speed_notes(selection)
    template = str(selection.get("prompt_template") or "").strip()
    prompt = str(selection.get("prompt") or selection.get("instructions") or "").strip()
    if template:
        execution = template.replace("{text}", text_value) if "{text}" in template else f"{template}\n\nText:\n{text_value}"
    elif prompt:
        execution = prompt.replace("{text}", text_value) if "{text}" in prompt else f"{prompt}\n\nText:\n{text_value}"
    else:
        execution = "\n\n".join([
            "Voice direction:\nUse a natural Chinese short-video talking-head delivery. The voice should feel close, conversational, warm, and real, not like a broadcaster or commercial announcer.",
            f"Speed and rhythm:\n{'；'.join(speed_notes)}。",
            "Delivery constraints:\nRead only the provided script. Do not add greetings, explanations, sound effects, extra narration, or generated background audio. Keep the emotion restrained and believable.",
            f"Text:\n{text_value}",
        ])
    return execution.strip(), speed_notes


def variant_shot_dir(workspace: Path, shot_id: str, variant_id: str) -> Path:
    return workspace / "Assets" / variant_id / safe_name(shot_id)


def final_prompt_package_rel(shot: dict[str, Any], shot_id: str, variant_id: str) -> str:
    package_ref = shot.get("final_prompt_package") if isinstance(shot.get("final_prompt_package"), dict) else {}
    return str(package_ref.get("path") or f"Assets/{variant_id}/{safe_name(shot_id)}/final_prompt_package.json").strip()


def builder_section_dir(workspace: Path, kind: str) -> Path:
    return workspace / "consistency_references" / ("host" if kind == "host" else "product")


def builder_output_name(kind: str) -> str:
    return "HOST.png" if kind == "host" else "PRODUCT.png"


def builder_manifest_path(workspace: Path, kind: str) -> Path:
    section = "host" if kind == "host" else "product"
    return builder_section_dir(workspace, kind) / f"{section}_reference_manifest.json"


def load_builder_manifest(workspace: Path, kind: str) -> dict[str, Any]:
    manifest_path = builder_manifest_path(workspace, kind)
    if not manifest_path.exists():
        return {}
    try:
        parsed = read_json(manifest_path)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def resolved_builder_reference(workspace: Path, kind: str) -> dict[str, Any]:
    manifest_path = builder_manifest_path(workspace, kind)
    manifest = load_builder_manifest(workspace, kind)
    output_rel = str(manifest.get("output") or f"consistency_references/{'host' if kind == 'host' else 'product'}/{builder_output_name(kind)}").strip()
    output_path = workspace / output_rel
    return {
        "kind": kind,
        "rel_path": output_rel,
        "path": str(output_path),
        "exists": output_path.exists() and output_path.is_file(),
        "manifest_path": rel(workspace, manifest_path),
    }


def attach_reference_manifests(workspace: Path, dependencies: dict[str, Any]) -> dict[str, Any]:
    next_dependencies = dict(dependencies)
    for kind in ("host", "product"):
        key = f"{kind}_reference"
        item = dict(next_dependencies.get(key) or {})
        item["manifest"] = load_builder_manifest(workspace, kind)
        next_dependencies[key] = item
    return next_dependencies


def guide_path() -> Path:
    return Path(__file__).with_name("prompt_references") / GUIDE_NAME


def scene_source_frame_rel(shot: dict[str, Any], mark: dict[str, Any]) -> str:
    keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
    for key in ("first", "single"):
        value = str(keyframes.get(key) or "").strip()
        if value:
            return value
    paths = keyframes.get("paths") if isinstance(keyframes.get("paths"), list) else []
    for value in paths:
        if str(value or "").strip():
            return str(value).strip()
    scene_id = scene_id_of(mark)
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    for frame in reference.get("keyframes") or []:
        if not isinstance(frame, dict):
            continue
        frame_mark = frame.get("scene_mark") if isinstance(frame.get("scene_mark"), dict) else {}
        if str(frame_mark.get("scene_mark_id") or "") == scene_id and str(frame_mark.get("role") or "") in {"first", "single"}:
            value = str(frame.get("path") or "").strip()
            if value:
                return value
    return ""


def scene_reference_image(workspace: Path, shot: dict[str, Any], shot_id: str, mark: dict[str, Any], variant_id: str) -> dict[str, Any]:
    scene_id = scene_id_of(mark)
    source_rel = scene_source_frame_rel(shot, mark)
    source_path = workspace / source_rel if source_rel else None
    target_path = variant_shot_dir(workspace, shot_id, variant_id) / safe_name(scene_id) / "first.png"
    return {
        "scene_mark_id": scene_id,
        "rel_path": source_rel,
        "path": str(source_path) if source_path else "",
        "exists": bool(source_rel),
        "target_rel_path": rel(workspace, target_path),
        "target_path": str(target_path),
    }


def scene_has_existing_first_frame(workspace: Path, mark: dict[str, Any], scene_ref: dict[str, Any]) -> bool:
    plan_d = mark.get("plan_d") if isinstance(mark.get("plan_d"), dict) else {}
    replacement = plan_d.get("replacement_first_frame") if isinstance(plan_d.get("replacement_first_frame"), dict) else {}
    selected = str(replacement.get("selected_image") or "").strip()
    if selected:
        selected_path = Path(selected)
        if not selected_path.is_absolute():
            selected_path = workspace / selected_path
        if selected_path.exists() and selected_path.is_file():
            return True
    target_path = Path(str(scene_ref.get("target_path") or ""))
    return target_path.exists() and target_path.is_file()


def dependency_report(workspace: Path, shot: dict[str, Any], shot_id: str, variant_id: str) -> dict[str, Any]:
    marks = scene_marks_for_shot(shot)
    scenes = [scene_reference_image(workspace, shot, shot_id, mark, variant_id) for mark in marks if scene_id_of(mark)]
    guide = guide_path()
    return {
        "workspace": str(workspace),
        "shot_id": shot_id,
        "variant_id": variant_id,
        "rebuild_shot_plan": {"rel_path": "rebuild_shot_plan.json", "exists": (workspace / "rebuild_shot_plan.json").exists()},
        "guide": {"path": str(guide), "exists": guide.exists() and guide.is_file()},
        "host_reference": resolved_builder_reference(workspace, "host"),
        "product_reference": resolved_builder_reference(workspace, "product"),
        "scene_reference_images": scenes,
        "scene_count": len(marks),
    }


def blocking_errors(dependencies: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not dependencies.get("rebuild_shot_plan", {}).get("exists"):
        errors.append("Missing rebuild_shot_plan.json.")
    if not dependencies.get("guide", {}).get("exists"):
        errors.append(f"Missing Plan D prompt guide: {dependencies.get('guide', {}).get('path')}")
    if not dependencies.get("host_reference", {}).get("exists"):
        errors.append(f"Missing host consistency reference image: {dependencies.get('host_reference', {}).get('rel_path')}")
    if not dependencies.get("product_reference", {}).get("exists"):
        errors.append(f"Missing product consistency reference image: {dependencies.get('product_reference', {}).get('rel_path')}")
    if not dependencies.get("scene_count"):
        errors.append("Shot has no scene marks.")
    if dependencies.get("scene_count") != len(dependencies.get("scene_reference_images") or []):
        errors.append("One or more scene marks are missing scene_mark_id.")
    for item in dependencies.get("scene_reference_images") or []:
        if not item.get("exists"):
            errors.append(f"Missing source first-frame reference for {item.get('scene_mark_id')}. Confirm the scene has keyframes.first or keyframes.single in rebuild_shot_plan.json.")
    return errors


def context_errors(args: argparse.Namespace, use_run_model: bool) -> list[str]:
    if not use_run_model:
        return []
    errors: list[str] = []
    if not str(args.task_id or "").strip():
        errors.append("Missing --task-id. Plan D image prompts are generated by the Session run model and require OC-Rebuild Task context.")
    return errors


def load_final_prompt_package_rel(shot: dict[str, Any], shot_id: str, variant_id: str) -> str:
    return final_prompt_package_rel(shot, shot_id, variant_id)


def nested_reference_match(mark: dict[str, Any]) -> dict[str, Any]:
    direct = mark.get("storyboard_reference_match") if isinstance(mark.get("storyboard_reference_match"), dict) else {}
    if direct:
        return direct
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    nested = desc.get("reference_match") if isinstance(desc.get("reference_match"), dict) else {}
    return nested


def prompt_style_reference_workspace(mark: dict[str, Any]) -> Path | None:
    match = nested_reference_match(mark)
    raw = str(match.get("reference_workspace") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.exists() and path.is_dir() else None


def prompt_style_reference_shot_ids(mark: dict[str, Any]) -> list[str]:
    match = nested_reference_match(mark)
    coverage = match.get("reference_coverage") if isinstance(match.get("reference_coverage"), list) else []
    shot_ids: list[str] = []
    for item in coverage:
        if not isinstance(item, dict):
            continue
        shot_id = str(item.get("reference_shot_id") or "").strip()
        if shot_id and shot_id not in shot_ids:
            shot_ids.append(shot_id)
    direct = str(match.get("reference_shot_id") or mark.get("source_shot_id") or "").strip()
    if direct and direct not in shot_ids:
        shot_ids.insert(0, direct)
    return shot_ids


def prompt_style_excerpt(text: str, max_chars: int = 6500) -> str:
    raw = str(text or "").strip()
    marker = "Model prompt:"
    if marker in raw:
        raw = raw.split(marker, 1)[1].strip()
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars].rstrip() + "\n...[truncated style example]"


def load_plan_d_prompt_style_examples(mark: dict[str, Any], variant_id: str, limit: int = 3) -> list[dict[str, str]]:
    reference_workspace = prompt_style_reference_workspace(mark)
    if not reference_workspace:
        return []
    preferred_shot_ids = prompt_style_reference_shot_ids(mark)
    candidates: list[Path] = []
    for shot_id in preferred_shot_ids:
        candidates.extend(sorted((reference_workspace / "Assets" / variant_id / safe_name(shot_id)).glob("*/codex_imagegen_prompt.txt")))
    if len(candidates) < limit:
        candidates.extend(sorted((reference_workspace / "Assets" / variant_id).glob("shot_*/shot_*_scene_*/codex_imagegen_prompt.txt")))
    seen: set[Path] = set()
    examples: list[dict[str, str]] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not path.exists() or not path.is_file():
            continue
        seen.add(resolved)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(errors="ignore")
        examples.append({
            "path": rel(reference_workspace, path),
            "reference_workspace": str(reference_workspace),
            "usage": "Task #5 Plan D 12_00 prompt style example only; not an image reference and not a scene content source.",
            "excerpt": prompt_style_excerpt(text),
        })
        if len(examples) >= limit:
            break
    return examples


def prompt_for_scene(shot: dict[str, Any], mark: dict[str, Any], refs: dict[str, Any], scene_ref: dict[str, Any], guide_rel_path: str) -> str:
    shot_id = shot_id_of(shot)
    scene_id = scene_id_of(mark)
    narration = scene_text(shot, mark) or "(No narration text was provided for this scene.)"
    visual = scene_visual(mark) or "Keep the visible action and composition from TARGET_FRAME."
    return "\n\n".join([
        f"Task:\nCreate a realistic vertical 9:16 Plan D replacement image for Shot {shot_id}, Scene {scene_id}. This is the first frame / replacement frame for a rebuilt short-video scene, not a poster, moodboard, product board, or advertisement layout.",
        "Input images:\n"
        f"- TARGET_FRAME: {scene_ref['rel_path']} (use this as the editable scene frame: composition, camera distance, pose, hand placement, room geometry, light, shadows, and phone-video texture).\n"
        f"- HOST_REFERENCE: {refs['host_reference']['rel_path']} (use only for the host's identity, face, hair, outfit direction, age impression, and natural short-video presence).\n"
        f"- PRODUCT_REFERENCE: {refs['product_reference']['rel_path']} (use only for product identity: shape, package color, label placement, size relationship, and approved readable packaging details).\n"
        f"- Prompt guide: {guide_rel_path}",
        "Goal:\nUse TARGET_FRAME as the base frame. Replace the visible host and product with the approved host and product references while preserving the original scene layout. The result should look like the same casual phone-video moment, now rebuilt with the approved host/product identity.",
        f"Scene narration:\n{narration}",
        f"Visual execution:\n{visual}",
        "Replacement method:\n1. Preserve TARGET_FRAME's framing, camera angle, lens feel, lighting direction, background, surface geometry, perspective, and scene scale.\n2. Replace the person using HOST_REFERENCE for identity and styling, but keep the TARGET_FRAME pose, gaze direction, facial expression category, hand position, and body scale unless the scene clearly requires a tiny natural correction.\n3. Replace the product using PRODUCT_REFERENCE for package identity, but keep the TARGET_FRAME product position, hand contact, perspective, shadows, and occlusion logic.\n4. Clean the frame after replacement: remove subtitles, captions, UI overlays, account names, watermarks, floating labels, unreadable text, and unrelated logos. Keep packaging text only when it belongs to the approved product identity.",
        "Scene locks:\nKeep the same vertical 9:16 phone-video realism, indoor/outdoor environment, furniture/table/kitchen or room elements, lighting color, shadow direction, depth of field, object placement, and natural compression texture from TARGET_FRAME. Do not redesign the set.",
        "Host locks:\nThe host must stay consistent with HOST_REFERENCE: same person identity, age impression, face structure, hair, outfit direction, skin tone, and natural social-video realism. Avoid beauty-filter exaggeration, studio portrait lighting, duplicate faces, distorted hands, extra fingers, or a different person.",
        "Product locks:\nThe product must stay consistent with PRODUCT_REFERENCE: same product category, package silhouette, color blocks, label hierarchy, cap/top/bottle/box structure, scale, and brand-safe readable details. Do not invent a new brand, new medical label, new efficacy text, or extra product variants.",
        "Negative requirements:\nNo subtitles. No watermarks. No UI text. No before/after claims. No medical claims. No authority endorsement props. No exaggerated efficacy cues. No generated ad copy. No poster composition. No product-board layout. No collage. No surreal lighting. No plastic skin. No warped packaging. No mismatched hands or shadows.",
        "Output:\nReturn one final realistic image matching TARGET_FRAME's aspect ratio and camera feel. The image should be immediately usable as the Scene first frame for Plan D image/video generation.",
    ])


REQUIRED_IMAGE_PROMPT_SECTIONS = [
    "Input image files",
    "Input image roles",
    "Priority rules",
    "Task",
    "Exact scene narration",
    "Scene locks",
    "Host locks",
    "Product locks",
    "Replacement method",
    "Negative requirements",
    "Output",
]


def missing_prompt_sections(prompt: str) -> list[str]:
    lowered = str(prompt or "").lower()
    return [section for section in REQUIRED_IMAGE_PROMPT_SECTIONS if section.lower() not in lowered]


def normalize_image_prompt(
    shot: dict[str, Any],
    mark: dict[str, Any],
    image_prompt: str,
    dependencies: dict[str, Any],
    scene_ref: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    narration = scene_text(shot, mark) or "(No narration text was provided for this scene.)"
    scene_id = scene_id_of(mark)
    header = "\n\n".join([
        "Input image files:\n"
        f"- TARGET_FRAME: {scene_ref['rel_path']}\n"
        f"- HOST_REFERENCE: {dependencies['host_reference']['rel_path']}\n"
        f"- PRODUCT_REFERENCE: {dependencies['product_reference']['rel_path']}\n"
        f"- HOST_MANIFEST: {dependencies['host_reference'].get('manifest_path', '')}\n"
        f"- PRODUCT_MANIFEST: {dependencies['product_reference'].get('manifest_path', '')}",
        "Input image roles:\n"
        "- TARGET_FRAME: current Task / current Scene editable base frame. Preserve its composition, camera angle, room geometry, head/shoulder placement, pose category, hand positions, product positions, scale, perspective, occlusion, lighting, shadows, and phone-video texture. Do not redesign it.\n"
        "- HOST_REFERENCE: current Task identity and styling reference for the new host only; it wins for face, hair, clothing, microphone, skin tone, hand/nail style, and accessories.\n"
        "- PRODUCT_REFERENCE: current Task product identity reference only; it wins for exact packaging identity, brand/logo area, Chinese title hierarchy, box/sachet structure, colors, material, proportions, label direction, key numbers, UP graphic, and component relationship.\n"
        "- TASK5_PROMPT_STYLE: prompt-writing style reference only. Use Task #5 Plan D 12_00 wording patterns for section structure and hard replacement constraints; do not use Task #5 images or scene content as visual references.",
        "Priority rules:\n"
        "1. PRODUCT_REFERENCE wins for exact product identity, packaging, text hierarchy, shape, color, material, component structure, key label direction, and package family.\n"
        "2. HOST_REFERENCE wins for person identity, face, hair, clothing, microphone, skin tone, hand/nail style, and accessories.\n"
        "3. TARGET_FRAME wins for camera angle, framing, background geometry, head/body placement, pose category, hand/product placement, scale, perspective, occlusion, lighting, shadows, and phone-video texture.\n"
        "4. Task #5 prompt examples win only as writing style and constraint phrasing; they are not image references.\n"
        "5. Do not preserve TARGET_FRAME's original person identity, clothing, old product, subtitles, UI, watermark, or old brand when they conflict with HOST_REFERENCE or PRODUCT_REFERENCE.",
        f"Task:\nCreate one realistic vertical 9:16 Plan D replacement first-frame image for Shot {shot_id_of(shot)}, Scene {scene_id}. The result must look like a real phone-video frame, not a poster, product board, reference board, collage, or catalog image.",
        f"Exact scene narration:\n{narration}\nDo not render this narration as subtitles or on-screen text.",
    ])
    body = str(image_prompt or "").strip()
    if body.lower().startswith("input image files"):
        normalized = body
    else:
        normalized = header + "\n\nAuthored segmented image prompt:\n" + body
    missing = missing_prompt_sections(normalized)
    if missing:
        normalized += "\n\nRequired section checklist:\n" + "\n".join(f"- {section}: follow the constraints above exactly." for section in missing)
    return normalized, {"status": "passed" if not missing else "completed_with_section_repair", "missing_sections_repaired": missing}


def prompt_source_mode(args: argparse.Namespace) -> str:
    return "run_model" if not args.use_template_image_prompt else "template"


def image_prompt_authoring_request(
    context: dict[str, Any],
    shot: dict[str, Any],
    mark: dict[str, Any],
    refs: dict[str, Any],
    scene_ref: dict[str, Any],
    guide_text: str,
    prompt_style_examples: list[dict[str, str]] | None = None,
) -> str:
    host_manifest = refs.get("host_reference", {}).get("manifest") if isinstance(refs.get("host_reference"), dict) else {}
    product_manifest = refs.get("product_reference", {}).get("manifest") if isinstance(refs.get("product_reference"), dict) else {}
    payload = {
        "task": {
            "task_id": context.get("task_id"),
            "session_id": context.get("session_id"),
            "run_model_provider": context.get("run_model_provider"),
            "run_model_id": context.get("run_model_id"),
        },
        "shot": {
            "shot_id": shot_id_of(shot),
            "scene_mark_id": scene_id_of(mark),
            "narration": scene_text(shot, mark),
            "scene_visual_from_05_01": scene_visual(mark),
            "target_frame": scene_ref.get("rel_path"),
        },
        "references": {
            "target_frame": scene_ref.get("rel_path"),
            "host_reference": refs.get("host_reference", {}).get("rel_path"),
            "product_reference": refs.get("product_reference", {}).get("rel_path"),
            "host_manifest": host_manifest,
            "product_manifest": product_manifest,
        },
        "task5_plan_d_prompt_style_examples": prompt_style_examples or [],
        "existing_scene_description": mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {},
        "task_final_prompt": context.get("final_prompt"),
    }
    return """你是 OpenCrew Rebuild V1 Plan D 的图片提示词撰写器。

你只负责为 Codex built-in image_gen 生成一个 Scene 首帧图片提示词。不要生成 TTS 提示词，不要生成视频提示词，不要生成解释。

你会收到三张图：
1. TARGET_FRAME：当前 Task / 当前 Scene 的原视频帧，只用于锁定构图、机位、背景、姿势类别、手部/产品位置、灯光、手机视频质感。
2. HOST_REFERENCE：当前 Task 的新主播一致性参考，只用于锁定新人物身份、脸、发型、服装、麦克风、手部/肤色/配饰风格。
3. PRODUCT_REFERENCE：当前 Task 的新产品一致性参考，只用于锁定产品包装身份、盒型/条包、颜色、文字层级、材质和比例。

你还会收到 Task #5 / Plan D / 12_00 生成过的 image_prompt 样例。
这些样例只能作为“提示词范式参考”：学习它的分段结构、强约束句式、Replacement method 写法、Negative requirements 写法和硬淘汰语言。
严禁把 Task #5 样例当成图片参考；严禁复用 Task #5 的图片路径、台词、scene id 或旧产品内容。
最终 image_prompt 的图片依据只能是当前 Task 的 TARGET_FRAME、HOST_REFERENCE、PRODUCT_REFERENCE。

核心优先级必须写入最终 image_prompt：
1. HOST_REFERENCE wins for person identity, face, hair, clothing, microphone, skin tone, hand/nail style, and accessories.
2. PRODUCT_REFERENCE wins for product identity, packaging, text hierarchy, shape, color, material, and component structure.
3. TARGET_FRAME wins only for camera angle, framing, background geometry, pose category, hand/product placement, lighting, shadows, perspective, and phone-video texture.
4. Do not preserve TARGET_FRAME's original person identity, face, hair, clothing, chest graphic/logo, accessories, old product, subtitles, UI, watermark, or old brand.

最终 image_prompt 必须是分段的，适合直接交给图像模型使用。必须明确写出：
- Input image roles
- Priority rules
- Task
- Scene narration
- Scene locks
- Host locks
- Product locks
- Replacement method
- Negative requirements
- Output

严格要求：
- 只输出 JSON 对象，不要 Markdown，不要解释。
- JSON 必须包含字段：
  {
    "image_prompt": "完整分段提示词字符串",
    "prompt_authoring_notes": ["简短说明生成时使用了哪些依据"],
    "validation": {"status": "passed", "warnings": []}
  }
- image_prompt 必须保留真实竖屏 9:16 手机口播画面，不得写成参考板、海报、产品白底图或广告图。
- image_prompt 必须使用 Task #5 Plan D 样例的写法范式，尤其是 `Use TARGET_FRAME as the base scene`、`Preserve ... positions, angles, scale, perspective, occlusion, and hand-contact shadows` 这一类硬约束。
- image_prompt 必须明确：当前 Task 的 TARGET_FRAME 是 editable/base scene，不能重新设计房间、机位、人物位置、手势、产品位置或透视。
- 必须把 HOST manifest 中的新人物服装/发型/麦克风/禁止复制旧主播等约束写进去。
- 必须把 PRODUCT manifest 中的产品名、包装结构、颜色、条包/盒装、禁止旧产品/旧包装等约束写进去。
- Product locks 必须按 PRODUCT_REFERENCE 的包装身份精确锁定，不要只写泛化的“绿色盒子/绿色条包”；需要写品牌/logo 区域、中文标题层级、关键数字、UP 图形、银绿材质、盒型、条包结构、12g 标识、标签方向、组件关系等可见包装身份。
- 如果 TARGET_FRAME 与 HOST/PRODUCT 冲突，最终提示词必须明确 HOST/PRODUCT 覆盖 TARGET_FRAME 中旧人物旧产品。
- 不要把参考文档原文整段复制进 image_prompt；你要根据参考文档和 manifest 生成可执行提示词。

参考文档《提示词撰写指南_口播_人物产品一致性模型_GPT.MD》：
""" + guide_text[:20000] + "\n\n输入上下文：\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def analysis_workspace_from_source_package(workspace: Path, source_package: dict[str, Any], plan: dict[str, Any] | None = None) -> Path | None:
    source = source_package.get("source") if isinstance(source_package.get("source"), dict) else {}
    for value in (source.get("analysis_workspace"), source_package.get("analysis_workspace")):
        raw = str(value or "").strip()
        if raw:
            path = Path(raw).expanduser()
            return path if path.is_absolute() else workspace / path
    if isinstance(plan, dict):
        session_id = str(plan.get("analysis_session_id") or "").strip()
        if session_id:
            return workspace.parents[1] / session_id / "workspace"
    return None


def resolve_reference_path(workspace: Path, image_workspace: Path, value: str, source_package: dict[str, Any] | None = None, plan: dict[str, Any] | None = None) -> Path:
    path = Path(str(value or ""))
    if path.is_absolute():
        return path
    workspace_candidate = workspace / path
    if workspace_candidate.exists():
        return workspace_candidate
    source_package = source_package if isinstance(source_package, dict) else {}
    raw = str(value or "").strip()
    if raw.startswith("source.analysis_workspace/"):
        analysis_workspace = analysis_workspace_from_source_package(workspace, source_package, plan)
        if analysis_workspace:
            return analysis_workspace / raw.removeprefix("source.analysis_workspace/")
    analysis_workspace = analysis_workspace_from_source_package(workspace, source_package, plan)
    if analysis_workspace:
        analysis_candidate = analysis_workspace / path
        if analysis_candidate.exists():
            return analysis_candidate
    return image_workspace / path


def call_image_prompt_authoring_model(
    scene_tool: Any,
    context: dict[str, Any],
    workspace: Path,
    image_workspace: Path,
    source_package: dict[str, Any],
    plan: dict[str, Any],
    shot: dict[str, Any],
    mark: dict[str, Any],
    refs: dict[str, Any],
    scene_ref: dict[str, Any],
    guide_text: str,
    prompt_style_examples: list[dict[str, str]],
    timeout_seconds: int,
) -> dict[str, Any]:
    prompt_text = image_prompt_authoring_request(context, shot, mark, refs, scene_ref, guide_text, prompt_style_examples)
    image_paths = [
        resolve_reference_path(workspace, image_workspace, str(scene_ref.get("rel_path") or ""), source_package, plan),
        Path(str(refs.get("host_reference", {}).get("path") or "")),
        Path(str(refs.get("product_reference", {}).get("path") or "")),
    ]
    image_parts: list[dict[str, Any]] = []
    missing: list[str] = []
    for image_path in image_paths:
        if image_path.exists() and image_path.is_file():
            image_parts.append(scene_tool.image_file_part(image_path, workspace))
        else:
            missing.append(str(image_path))
    if len(image_parts) != 3:
        raise ToolError(f"Plan D image prompt authoring requires TARGET/HOST/PRODUCT images. Missing: {missing}")

    started_at = now_ms()
    scene_tool.request_opencode_json(
        context,
        "POST",
        f"/session/{context['opencode_session_id']}/prompt_async",
        {"parts": [{"type": "text", "text": prompt_text}] + image_parts, "model": {"providerID": context["run_model_provider"], "modelID": context["run_model_id"]}},
        query={"directory": context["workspace_dir"]},
        timeout=30,
    )
    deadline = time.time() + timeout_seconds
    response_text = ""
    parent_id = ""
    while time.time() < deadline:
        messages = scene_tool.request_opencode_json(context, "GET", f"/session/{context['opencode_session_id']}/message", None, query={"directory": context["workspace_dir"], "limit": "160"}, timeout=30) or []
        parent_id = parent_id or scene_tool.matching_user_prompt_id(messages, started_at, prompt_text)
        response_text = scene_tool.assistant_text_for_parent(messages, parent_id)
        if response_text:
            break
        time.sleep(1)
    if not response_text:
        raise ToolError(f"OpenCode timed out before generating Plan D image prompt for {shot_id_of(shot)} / {scene_id_of(mark)}")
    result = scene_tool.extract_json_object(response_text)
    image_prompt = str(result.get("image_prompt") or "").strip()
    if not image_prompt:
        raise ToolError(f"Run model returned empty image_prompt for {shot_id_of(shot)} / {scene_id_of(mark)}")
    return {
        "image_prompt": image_prompt,
        "prompt_authoring_notes": result.get("prompt_authoring_notes") if isinstance(result.get("prompt_authoring_notes"), list) else [],
        "validation": result.get("validation") if isinstance(result.get("validation"), dict) else {},
    }


def scene_reference_image_paths(
    workspace: Path,
    image_workspace: Path,
    source_package: dict[str, Any],
    plan: dict[str, Any],
    refs: dict[str, Any],
    scene_ref: dict[str, Any],
) -> dict[str, str]:
    return {
        "target_frame": str(resolve_reference_path(workspace, image_workspace, str(scene_ref.get("rel_path") or ""), source_package, plan)),
        "host_reference": str(refs.get("host_reference", {}).get("path") or ""),
        "product_reference": str(refs.get("product_reference", {}).get("path") or ""),
    }


def video_prompt_for_scene(shot: dict[str, Any], mark: dict[str, Any], scene_ref: dict[str, Any], host_ref: str, product_ref: str) -> str:
    shot_id = shot_id_of(shot)
    scene_id = scene_id_of(mark)
    narration = scene_text(shot, mark) or "(No narration text was provided for this scene.)"
    visual = scene_visual(mark) or "Keep the action subtle and natural from the reference image."
    return "\n\n".join([
        f"Task:\nCreate a realistic vertical 9:16 talking-head product video for Shot {shot_id}, Scene {scene_id}. Use the final replacement image as the first-frame anchor and maintain a casual phone-video look.",
        "Reference images:\n"
        f"- FIRST_FRAME_REFERENCE: {scene_ref['rel_path']} (primary scene anchor for composition, camera angle, lighting, room geometry, pose, hand/product placement, and phone-video texture).\n"
        f"- HOST_REFERENCE: {host_ref} (identity anchor only; keep the same host identity, face, hair, outfit direction, and natural presence).\n"
        f"- PRODUCT_REFERENCE: {product_ref} (product identity anchor only; keep package shape, color, label hierarchy, scale, and approved readable details).",
        f"Narration window:\nThe host is speaking this sentence during the scene: {narration}",
        f"Visual action:\n{visual}",
        "Motion design:\nUse subtle natural motion only: small head movement, blinking, micro facial expression changes, slight hand or product movement, and gentle handheld camera micro-movement. Keep the body, hands, product, shadows, and background coherent across the clip.",
        "Continuity locks:\nPreserve the first frame identity, product packaging, room, lighting, camera distance, perspective, and real phone-camera texture. Do not cut to a new scene or redesign the layout.",
        "Audio and lip-sync notes:\nThe final locked TTS audio will drive lip sync later. If the video model generates audio, it should not add conflicting speech, music, sound effects, or extra narration.",
        "Negative requirements:\nNo subtitles, captions, watermarks, UI overlays, floating text, account names, unrelated logos, medical claims, before/after claims, authority endorsement props, exaggerated efficacy cues, cinematic camera moves, poster styling, product-board layout, or ad-copy text.",
        "Output:\nReturn one natural image-to-video clip that starts from the reference frame and is ready for TTS-driven lip-sync replacement.",
    ])


def sync_package_to_plan(shot: dict[str, Any], package: dict[str, Any], rel_path: str) -> None:
    timestamp = now_ms()
    existing_ref = shot.get("final_prompt_package") if isinstance(shot.get("final_prompt_package"), dict) else {}
    scenes = [item for item in package.get("scenes") or [] if isinstance(item, dict)]
    shot["final_prompt_package"] = {
        **existing_ref,
        "path": rel_path,
        "updated_at": timestamp,
        "updated_by": TOOL_ID,
        "scene_count": len(scenes),
        "prompt_package_version": PROMPT_PACKAGE_VERSION,
    }
    marks = scene_marks_for_shot(shot)
    scenes_by_id = {str(item.get("scene_mark_id") or ""): item for item in scenes}
    for mark in marks:
        scene_id = scene_id_of(mark)
        scene = scenes_by_id.get(scene_id)
        if not scene:
            continue
        final_prompts = mark.get("final_prompts") if isinstance(mark.get("final_prompts"), dict) else {}
        final_prompts["image_prompt"] = scene.get("image_prompt") or ""
        final_prompts["image_prompt_authoring"] = scene.get("image_prompt_authoring") or {}
        final_prompts["video_prompt"] = scene.get("video_prompt") or ""
        mark["final_prompts"] = final_prompts


def build_prompts(args: argparse.Namespace) -> dict[str, Any]:
    workspace = workspace_path(args.workspace)
    plan_path = workspace / args.input
    if not plan_path.exists():
        raise ToolError(f"Input shot plan not found: {plan_path}")
    if len(args.shot_id) != 1:
        raise ToolError("Exactly one --shot-id is required.")
    shot_id = str(args.shot_id[0]).strip()
    plan = read_json(plan_path)
    if not isinstance(plan, dict):
        raise ToolError("rebuild_shot_plan.json must be a JSON object.")
    shot = find_shot(plan, shot_id)
    dependencies = dependency_report(workspace, shot, shot_id, args.variant_id)
    use_run_model = prompt_source_mode(args) == "run_model"
    errors = blocking_errors(dependencies) + context_errors(args, use_run_model)
    if errors:
        raise BlockedError(errors, dependencies)
    if args.check_dependencies_only:
        return {"status": "ready", "tool": TOOL_ID, "dependencies": dependencies, "image_prompt_source": prompt_source_mode(args)}

    scene_tool: Any | None = None
    context: dict[str, Any] = {}
    source_package: dict[str, Any] = {}
    image_workspace = workspace
    guide_text = ""
    prompt_dependencies = dependencies
    if use_run_model:
        scene_tool = load_scene_prompt_tool()
        context = scene_tool.fetch_rebuild_context(scene_tool.resolve_database_url(args), int(args.task_id))
        scene_tool.validate_rebuild_context_for_workspace(workspace, plan, context)
        source_package_path = workspace / args.source_package
        if source_package_path.exists():
            loaded_source_package = read_json(source_package_path)
            source_package = loaded_source_package if isinstance(loaded_source_package, dict) else {}
        package_workspace = analysis_workspace_from_source_package(workspace, source_package, plan) or scene_tool.source_workspace_from_package(workspace, source_package)
        image_workspace = package_workspace.expanduser() if package_workspace else workspace
        guide_text = guide_path().read_text(encoding="utf-8")
        prompt_dependencies = attach_reference_manifests(workspace, dependencies)

    generated_at = now_ms()
    package_rel = load_final_prompt_package_rel(shot, shot_id, args.variant_id)
    marks = scene_marks_for_shot(shot)
    refs_by_scene = {str(item.get("scene_mark_id") or ""): item for item in dependencies.get("scene_reference_images") or []}
    guide_rel = rel(Path(__file__).resolve().parents[3], guide_path())
    tts_prompt, speed_notes = build_tts_prompt(shot)

    next_scenes: list[dict[str, Any]] = []
    prompt_authoring_results: list[dict[str, Any]] = []
    for mark in marks:
        scene_id = scene_id_of(mark)
        if not scene_id:
            continue
        scene_ref = refs_by_scene[scene_id]
        prompt_authoring: dict[str, Any] = {"source": "template", "notes": [], "validation": {}}
        has_existing_first_frame = scene_has_existing_first_frame(workspace, mark, scene_ref)
        if has_existing_first_frame:
            image_prompt = ""
            prompt_authoring = {
                "source": "skipped_existing_first_frame",
                "provider": "",
                "model": "",
                "notes": [
                    "Skipped image prompt authoring because this scene already has a first.png / selected replacement first frame. Only TTS and video prompts were refreshed."
                ],
                "validation": {"status": "skipped_existing_first_frame", "warnings": []},
            }
        elif use_run_model:
            assert scene_tool is not None
            prompt_style_examples = load_plan_d_prompt_style_examples(mark, args.variant_id)
            model_result = call_image_prompt_authoring_model(
                scene_tool,
                context,
                workspace,
                image_workspace,
                source_package,
                plan,
                shot,
                mark,
                prompt_dependencies,
                scene_ref,
                guide_text,
                prompt_style_examples,
                int(args.timeout_seconds),
            )
            image_prompt = str(model_result["image_prompt"])
            prompt_authoring = {
                "source": "run_model",
                "provider": context.get("run_model_provider"),
                "model": context.get("run_model_id"),
                "notes": model_result.get("prompt_authoring_notes") or [],
                "validation": model_result.get("validation") or {},
                "prompt_style_examples": prompt_style_examples,
            }
        else:
            image_prompt = prompt_for_scene(shot, mark, dependencies, scene_ref, guide_rel)
        if not has_existing_first_frame:
            image_prompt, structure_validation = normalize_image_prompt(shot, mark, image_prompt, dependencies, scene_ref)
            existing_validation = prompt_authoring.get("validation") if isinstance(prompt_authoring.get("validation"), dict) else {}
            prompt_authoring["validation"] = {
                **existing_validation,
                "structure": structure_validation,
            }
            if structure_validation.get("missing_sections_repaired"):
                notes = prompt_authoring.get("notes") if isinstance(prompt_authoring.get("notes"), list) else []
                prompt_authoring["notes"] = [*notes, "Tool normalized the image prompt into required sections and injected explicit TARGET/HOST/PRODUCT reference file paths."]
        video_prompt = video_prompt_for_scene(shot, mark, scene_ref, dependencies["host_reference"]["rel_path"], dependencies["product_reference"]["rel_path"])
        prompt_authoring_results.append({"scene_mark_id": scene_id, **prompt_authoring})
        next_scenes.append({
            "scene_mark_id": scene_id,
            "reference_image": scene_ref["rel_path"],
            "reference_image_paths": scene_reference_image_paths(workspace, image_workspace, source_package, plan, dependencies, scene_ref),
            "image_prompt_status": "skipped_existing_first_frame" if has_existing_first_frame else "generated",
            "image_prompt": image_prompt,
            "video_prompt": video_prompt,
            "image_prompt_authoring": prompt_authoring,
        })
    package = {
        "shot_id": shot_id,
        "updated_at": generated_at,
        "prompt_package_version": PROMPT_PACKAGE_VERSION,
        "image_prompt_source": prompt_source_mode(args),
        "references": {
            "host_image": dependencies["host_reference"]["rel_path"],
            "product_image": dependencies["product_reference"]["rel_path"],
            "host_manifest": dependencies["host_reference"]["manifest_path"],
            "product_manifest": dependencies["product_reference"]["manifest_path"],
            "image_prompt_guide": guide_rel,
        },
        "tts_prompt": tts_prompt,
        "tts_speed_notes": speed_notes,
        "scenes": next_scenes,
    }

    package_path = workspace / package_rel
    write_json(package_path, package)
    sync_package_to_plan(shot, package, package_rel)
    write_json(plan_path, plan)

    report = {
        "status": "completed",
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "generated_at": generated_at,
        "shot_id": shot_id,
        "variant_id": args.variant_id,
        "dependencies": dependencies,
        "final_prompt_package": package_rel,
        "scene_count": len(next_scenes),
        "image_prompt_source": prompt_source_mode(args),
        "prompt_authoring_results": prompt_authoring_results,
    }
    report_rel = f"Assets/{args.variant_id}/{safe_name(shot_id)}/reports/plan_d_12_00_replacement_image_prompt_build.json"
    write_json(workspace / report_rel, report)
    return {"status": "completed", "tool": TOOL_ID, "dependencies": dependencies, "result": {**report, "report": report_rel}}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    parser.add_argument("--workspace", required=True, help="Session workspace directory.")
    parser.add_argument("--task-id", default="", help="OpenCrew task id, recorded for caller context.")
    parser.add_argument("--session-id", default="", help="OpenCrew session id, recorded for caller context.")
    parser.add_argument("--shot-id", action="append", default=[], help="Target shot id. Required exactly once.")
    parser.add_argument("--input", default="rebuild_shot_plan.json", help="Shot plan JSON path relative to workspace.")
    parser.add_argument("--output", default="rebuild_shot_plan.json", help="Reserved for registry compatibility; output remains rebuild_shot_plan.json.")
    parser.add_argument("--variant-id", default=DEFAULT_VARIANT_ID, help="Asset variant id.")
    parser.add_argument("--source-package", default="source_package.json", help="Reserved source package name for registry compatibility.")
    parser.add_argument("--database-url", default="", help="OpenCrew database URL. Defaults to --database-url-env, OPENCREW_DATABASE_URL, DATABASE_URL, then local default.")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV, help="Environment variable containing the OpenCrew database URL.")
    parser.add_argument("--timeout-seconds", type=int, default=300, help="Timeout for Session run model image prompt authoring.")
    parser.add_argument("--use-template-image-prompt", action="store_true", help="Fallback: use local template image prompts instead of Session run model authoring.")
    parser.add_argument("--force", action="store_true", help="Rebuild prompts even when an image prompt already exists.")
    parser.add_argument("--check-dependencies-only", action="store_true", help="Validate dependencies without writing prompt files.")
    parser.add_argument("--print-json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = build_prompts(args)
        if args.print_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"{TOOL_ID}: {payload['status']}")
        return 0
    except BlockedError as exc:
        payload = {"status": "blocked", "tool": TOOL_ID, "blocking_errors": exc.errors, "dependencies": exc.dependencies}
        if args.print_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("\n".join(exc.errors), file=sys.stderr)
        return 2
    except Exception as exc:
        payload = {"status": "failed", "tool": TOOL_ID, "error": str(exc)}
        if args.print_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
