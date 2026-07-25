from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


TOOL_NAME = "05_01_VideoPlanGenerator"
TOOL_VERSION = "0.1.0"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
CONSISTENCY_DIR_REL = f"{CONTEXT_DIR_NAME}/Consistency"
TOOL_DIR_NAME = "S8_05_01_VideoPlanGenerator"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_STORYBOARD_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_7_srt_storyboard.json"
WORKING_PARAMS_REL = f"{TOOL_DIR_NAME}/Working/InputParams_video_generation_plan.json"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
OUTPUT_PLAN_REL = f"{TOOL_DIR_NAME}/Output/video_generation_plan.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SESSION_STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
SESSION_PLAN_REL = "SessionOutput/storyboard/video_generation_plan.json"
STORYBOARD_SEED_REL = "SessionOutput/storyboard/storyboard_seed.json"
STORYBOARD_WORKING_DIR_REL = "SessionOutput/storyboard/Working"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm")
JSON_EXTS = (".json",)
VIDEO_MODEL_MAX_SECONDS = 15.0
DANCE_MIMIC_WORKFLOW_ID = "dance_mimic_v1"
TALKING_HEAD_WORKFLOW_ID = "person_talking_head_v1"
DANCE_MIMIC_VIDEO_GENERATION_MODE = "dance_mimic_reference_video"
DANCE_MIMIC_VIDEO_PROVIDER = "openrouter"
DANCE_MIMIC_VIDEO_MODEL = "bytedance/seedance-2.0"
DANCE_MIMIC_VIDEO_MODEL_ALIAS = "MaxSR2"
DANCE_MIMIC_REFERENCE_MODE = "input_references"
DANCE_MIMIC_PROMPT_TEMPLATE = "Video_SDR2V_DanceMimic.md"
DANCE_MIMIC_REFERENCE_VIDEO_ROLE = "dance_mimic_segment_motion_reference"
CONSISTENCY_REFERENCES = (
    {"kind": "host", "label": "人物一致性", "stem": "HOST"},
    {"kind": "product", "label": "产品一致性", "stem": "Product"},
)
SECRET_PATTERNS = (
    "postgresql://",
    "postgresql+psycopg://",
    "password",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "bearer ",
    "cookie",
)


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Args:
    workspace: str
    target_type: str
    shot_id: str
    scene_id: str
    max_video_seconds: float
    min_video_seconds: float
    split_tolerance_seconds: float
    force: bool
    resume: bool
    print_json: bool


@dataclass(frozen=True)
class SceneRef:
    shot_index: int
    scene_index: int
    global_index: int
    shot: dict[str, Any]
    scene: dict[str, Any]


@dataclass(frozen=True)
class VisualSource:
    source_type: str
    source_path: str
    requires_generated_image_before_video: bool
    planned_generated_image_path: str
    materialize_first_frame: dict[str, Any]
    existing_video: dict[str, Any]
    need_image_prompt: bool
    need_image: bool
    need_video_prompt: bool
    need_video: bool
    image_prompt_path: str = ""


@dataclass(frozen=True)
class TailSource:
    source_type: str
    source_path: str
    depends_on_segment_id: str
    depends_on_video_path: str
    depends_on_tail_frame_path: str
    available: bool
    continuation_allowed: bool = True


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def plan_hash(plan: dict[str, Any]) -> str:
    payload = json_safe(plan)
    if isinstance(payload, dict):
        payload = {key: value for key, value in payload.items() if key not in {"plan_hash", "plan_run_id", "created_at"}}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser() if raw_workspace else Path.cwd()
    try:
        return workspace.resolve()
    except Exception:
        return workspace.absolute()


def validate_workspace(workspace: Path) -> None:
    if not workspace.exists():
        raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise BlockedError("workspace_not_directory", f"Workspace is not a directory: {workspace}")


def stable_hash(payload: Any) -> str:
    raw = json.dumps(json_safe(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def text_value(value: Any) -> str:
    return str(value or "").strip()


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def sanitize_asset_key(value: str, fallback: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(value or "").strip())
    text = "_".join(part for part in text.split("_") if part)
    return text or fallback


def dialogue_key(dialogue: dict[str, Any], index: int) -> str:
    asset_key = sanitize_asset_key(text_value(dialogue.get("dialogue_asset_key")), "")
    if not asset_key:
        raise BlockedError("dialogue_asset_key_missing", f"Dialogue at index {index} has no dialogue_asset_key.")
    return asset_key


def working_path_for(asset_key: str, suffix: str) -> str:
    return f"{STORYBOARD_WORKING_DIR_REL}/{asset_key}_{suffix}"


def is_workspace_relative(path: str) -> bool:
    return bool(path) and not Path(path).is_absolute()


def workspace_path(workspace: Path, rel: str) -> Path:
    path = Path(rel)
    return path if path.is_absolute() else workspace / path


def file_exists(workspace: Path, rel: str) -> bool:
    if not rel:
        return False
    return workspace_path(workspace, rel).exists()


def dance_mimic_seed_by_asset_key(workspace: Path) -> dict[str, dict[str, Any]]:
    seed_path = workspace / STORYBOARD_SEED_REL
    if not seed_path.exists():
        return {}
    try:
        seed = read_json(seed_path)
    except Exception:
        return {}
    if not isinstance(seed, dict):
        return {}
    segments = list_value(seed.get("segments"))
    if text_value(seed.get("workflow_id")) != DANCE_MIMIC_WORKFLOW_ID and not any(
        isinstance(segment, dict) and text_value(segment.get("reference_video_path"))
        for segment in segments
    ):
        return {}

    mapped: dict[str, dict[str, Any]] = {}
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        key = text_value(segment.get("dialogue_asset_key") or segment.get("asset_key"))
        reference_video_path = text_value(segment.get("reference_video_path"))
        if not key:
            continue
        mapped[key] = {
            "video_generation_mode": text_value(segment.get("video_generation_mode")) or DANCE_MIMIC_VIDEO_GENERATION_MODE,
            "provider": DANCE_MIMIC_VIDEO_PROVIDER,
            "model": DANCE_MIMIC_VIDEO_MODEL,
            "model_alias": text_value(segment.get("model_alias")) or DANCE_MIMIC_VIDEO_MODEL_ALIAS,
            "reference_mode": DANCE_MIMIC_REFERENCE_MODE,
            "prompt_template": text_value(segment.get("prompt_template")) or DANCE_MIMIC_PROMPT_TEMPLATE,
            "reference_video_path": reference_video_path,
            "provider_reference_video_path": text_value(segment.get("provider_reference_video_path")),
            "source_face_masked_reference_video_path": text_value(segment.get("source_face_masked_reference_video_path")),
            "target_identity_image_path": text_value(segment.get("target_identity_image_path")),
            "provider_target_identity_image_path": text_value(segment.get("provider_target_identity_image_path")),
            "first_frame_image_path": text_value(segment.get("first_frame_image_path")),
            "source_target_identity_image_path": text_value(segment.get("source_target_identity_image_path")),
            "segment_audio_path": text_value(segment.get("segment_audio_path")),
            "segment_audio_source_path": text_value(segment.get("segment_audio_source_path")),
            "reference_video_role": text_value(segment.get("reference_video_role")) or DANCE_MIMIC_REFERENCE_VIDEO_ROLE,
            "privacy_grid_mode": bool(segment.get("privacy_grid_mode")),
            "reference_video_grid_applied": bool(segment.get("reference_video_grid_applied")),
            "target_identity_grid_applied": bool(segment.get("target_identity_grid_applied")),
            "effective_grid_scope": text_value(segment.get("effective_grid_scope")),
            "privacy_grid_manifest_path": text_value(segment.get("privacy_grid_manifest_path")),
            "prompt_contract": text_value(segment.get("prompt_contract")),
            "storyboard_seed_segment_id": text_value(segment.get("segment_id")),
            "storyboard_seed_path": STORYBOARD_SEED_REL,
        }
    return mapped


def dance_mimic_fields_from_segment(segment: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "video_generation_mode",
        "provider",
        "model",
        "model_alias",
        "reference_mode",
        "prompt_template",
        "reference_video_path",
        "provider_reference_video_path",
        "source_face_masked_reference_video_path",
        "target_identity_image_path",
        "provider_target_identity_image_path",
        "first_frame_image_path",
        "source_target_identity_image_path",
        "segment_audio_path",
        "segment_audio_source_path",
        "reference_video_role",
        "effective_grid_scope",
        "privacy_grid_manifest_path",
        "prompt_contract",
        "storyboard_seed_segment_id",
        "storyboard_seed_path",
    )
    fields = {key: segment[key] for key in keys if text_value(segment.get(key))}
    nested = dict_value(segment.get("dance_mimic"))
    if nested:
        fields["dance_mimic"] = nested
    return fields


def apply_dance_mimic_seed_to_shots(workspace: Path, shots: list[dict[str, Any]]) -> int:
    seed_by_asset = dance_mimic_seed_by_asset_key(workspace)
    if not seed_by_asset:
        return 0
    applied = 0
    for shot in shots:
        for scene in list_value(shot.get("scenes")):
            if not isinstance(scene, dict):
                continue
            for segment in list_value(scene.get("segments")):
                if not isinstance(segment, dict):
                    continue
                candidate_keys = [text_value(segment.get("asset_key"))] + [
                    text_value(item)
                    for item in list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids"))
                ]
                seed_segment = next((seed_by_asset[key] for key in candidate_keys if key in seed_by_asset), None)
                if not seed_segment:
                    continue
                segment.update(seed_segment)
                segment["dance_mimic"] = {
                    "workflow_id": DANCE_MIMIC_WORKFLOW_ID,
                    "reference_video_path": seed_segment["reference_video_path"],
                    "provider_reference_video_path": seed_segment.get("provider_reference_video_path", ""),
                    "reference_video_role": seed_segment["reference_video_role"],
                    "target_identity_image_path": seed_segment.get("target_identity_image_path", ""),
                    "provider_target_identity_image_path": seed_segment.get("provider_target_identity_image_path", ""),
                    "reference_mode": DANCE_MIMIC_REFERENCE_MODE,
                    "provider": DANCE_MIMIC_VIDEO_PROVIDER,
                    "model": DANCE_MIMIC_VIDEO_MODEL,
                    "model_alias": seed_segment["model_alias"],
                    "prompt_template": seed_segment["prompt_template"],
                    "storyboard_seed_path": STORYBOARD_SEED_REL,
                    "storyboard_seed_segment_id": seed_segment["storyboard_seed_segment_id"],
                    "privacy_grid_mode": bool(seed_segment.get("privacy_grid_mode")),
                    "reference_video_grid_applied": bool(seed_segment.get("reference_video_grid_applied")),
                    "target_identity_grid_applied": bool(seed_segment.get("target_identity_grid_applied")),
                    "effective_grid_scope": seed_segment.get("effective_grid_scope", ""),
                    "privacy_grid_manifest_path": seed_segment.get("privacy_grid_manifest_path", ""),
                    "prompt_contract": seed_segment.get("prompt_contract", ""),
                }
                if segment.get("status") != "blocked":
                    tasks = dict_value(segment.get("tasks"))
                    tasks["need_video"] = True
                    tasks["need_video_prompt"] = True
                    segment["tasks"] = tasks
                applied += 1
    return applied


def workspace_rel(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def existing_nonempty_file(workspace: Path, rel: str) -> str:
    if not rel:
        return ""
    path = workspace_path(workspace, rel)
    if path.exists() and path.is_file() and path.stat().st_size > 0:
        return workspace_rel(workspace, path)
    return ""


def existing_working_slot_path(workspace: Path, asset_key: str, slot: str, exts: tuple[str, ...]) -> str:
    if not asset_key:
        return ""
    normalized_exts = {ext.lower() for ext in exts}
    for ext in exts:
        found = existing_nonempty_file(workspace, f"{STORYBOARD_WORKING_DIR_REL}/{asset_key}_{slot}{ext}")
        if found:
            return found
    working_dir = workspace / STORYBOARD_WORKING_DIR_REL
    if not working_dir.exists():
        return ""
    prefix = f"{asset_key}_{slot}."
    for path in sorted(working_dir.glob(f"{asset_key}_{slot}.*")):
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        if path.name.startswith(prefix) and path.suffix.lower() in normalized_exts:
            return workspace_rel(workspace, path)
    return ""


def consistency_manifest_output(workspace: Path, kind: str) -> str:
    manifest_path = workspace / CONSISTENCY_DIR_REL / f"{kind}_manifest.json"
    if not manifest_path.exists():
        return ""
    try:
        manifest = read_json(manifest_path)
    except Exception:
        return ""
    if not isinstance(manifest, dict):
        return ""
    return existing_nonempty_file(workspace, text_value(manifest.get("output")))


def consistency_final_image(workspace: Path, item: dict[str, str]) -> str:
    for suffix in IMAGE_EXTS:
        found = existing_nonempty_file(workspace, f"{CONSISTENCY_DIR_REL}/{item['stem']}{suffix}")
        if found:
            return found
    return consistency_manifest_output(workspace, item["kind"])


def consistency_reference_status(workspace: Path) -> dict[str, Any]:
    references: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for item in CONSISTENCY_REFERENCES:
        output_path = consistency_final_image(workspace, item)
        available = bool(output_path)
        expected_paths = [f"{CONSISTENCY_DIR_REL}/{item['stem']}{suffix}" for suffix in IMAGE_EXTS]
        references.append({
            "kind": item["kind"],
            "label": item["label"],
            "available": available,
            "output_path": output_path,
            "expected_final_image_paths": expected_paths,
            "missing_reason": "" if available else "final_reference_image_missing",
        })
        if not available:
            missing.append({
                "kind": item["kind"],
                "label": item["label"],
                "code": f"{item['kind']}_consistency_reference_image_missing",
                "message": f"缺少{item['label']}最终结果图片，不阻碍 video plan 生成。",
            })
    return {
        "status": "ready" if not missing else "missing_reference_images",
        "references": references,
        "missing": missing,
        "blocking": False,
    }


def load_variables(workspace: Path) -> dict[str, Any]:
    path = workspace / VARIABLES_REL
    if not path.exists():
        raise BlockedError("variables_missing", f"Required SessionContext file is missing: {VARIABLES_REL}.")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    return payload


def load_storyboard(workspace: Path) -> dict[str, Any]:
    path = workspace / SESSION_STORYBOARD_REL
    if not path.exists():
        raise BlockedError("storyboard_missing", f"Required StoryBoard JSON is missing: {SESSION_STORYBOARD_REL}.")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("storyboard_invalid", f"{workspace_rel(workspace, path)} must contain a JSON object.")
    shots = payload.get("shots")
    if not isinstance(shots, list) or not shots:
        raise BlockedError("storyboard_shots_empty", f"{SESSION_STORYBOARD_REL} must contain non-empty shots[].")
    return payload


def validate_args(args: Args) -> None:
    if args.target_type not in {"scene", "shot", "task"}:
        raise BlockedError("target_type_invalid", "--target-type must be scene, shot, or task.")
    if args.target_type == "scene" and (not args.shot_id or not args.scene_id):
        raise BlockedError("scene_target_requires_ids", "--target-type scene requires --shot-id and --scene-id.")
    if args.target_type == "shot" and not args.shot_id:
        raise BlockedError("shot_target_requires_shot_id", "--target-type shot requires --shot-id.")
    if args.max_video_seconds <= 0:
        raise BlockedError("max_video_seconds_invalid", "--max-video-seconds must be greater than 0.")
    if args.min_video_seconds <= 0:
        raise BlockedError("min_video_seconds_invalid", "--min-video-seconds must be greater than 0.")
    if args.split_tolerance_seconds < 0:
        raise BlockedError("split_tolerance_seconds_invalid", "--split-tolerance-seconds must be greater than or equal to 0.")


def ensure_tool_dirs(workspace: Path) -> None:
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        f"{TOOL_DIR_NAME}/Report",
        "SessionOutput/storyboard",
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    cleanup_actions = result.setdefault("cleanup_actions", [])
    tool_dir = workspace / TOOL_DIR_NAME
    if tool_dir.exists():
        remove_path(tool_dir)
        cleanup_actions.append({"path": TOOL_DIR_NAME, "action": "removed_for_force_rerun"})
    session_plan = workspace / SESSION_PLAN_REL
    if session_plan.exists():
        remove_path(session_plan)
        cleanup_actions.append({"path": SESSION_PLAN_REL, "action": "removed_for_force_rerun"})


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": False,
        "requires_model_calls": False,
        "reads_session_context": [VARIABLES_REL],
        "writes_session_context": [],
        "created_files": [],
        "prepared_directories": [],
        "cleanup_actions": [],
        "inputs": {},
        "outputs": {},
        "summary": {},
        "warnings": [],
        "blocked_reasons": [],
        "resume": bool(args.resume),
        "force": bool(args.force),
        "updated_at": now_iso(),
    }


def add_block(result: dict[str, Any], code: str, message: str) -> None:
    result["status"] = "blocked"
    result.setdefault("blocked_reasons", []).append({"code": code, "message": message})


def scan_for_sensitive_output(payload: dict[str, Any]) -> list[dict[str, str]]:
    text = json.dumps(payload, ensure_ascii=False).lower()
    warnings: list[dict[str, str]] = []
    for pattern in SECRET_PATTERNS:
        if pattern in text:
            warnings.append({"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"})
    return warnings


def flatten_scenes(storyboard: dict[str, Any]) -> list[SceneRef]:
    refs: list[SceneRef] = []
    for shot_index, shot in enumerate(list_value(storyboard.get("shots"))):
        if not isinstance(shot, dict):
            continue
        for scene_index, scene in enumerate(list_value(shot.get("scenes"))):
            if isinstance(scene, dict):
                refs.append(SceneRef(shot_index=shot_index, scene_index=scene_index, global_index=len(refs), shot=shot, scene=scene))
    return refs


def selected_scenes(refs: list[SceneRef], args: Args) -> list[SceneRef]:
    if args.target_type == "task":
        return refs
    if args.target_type == "shot":
        selected = [ref for ref in refs if text_value(ref.shot.get("shot_id")) == args.shot_id]
        if not selected:
            raise BlockedError("shot_not_found", f"Shot not found: {args.shot_id}")
        return selected
    selected = [
        ref
        for ref in refs
        if text_value(ref.shot.get("shot_id")) == args.shot_id and text_value(ref.scene.get("scene_id")) == args.scene_id
    ]
    if not selected:
        raise BlockedError("scene_not_found", f"Scene not found: {args.shot_id}/{args.scene_id}")
    return selected


def scene_dialogues(scene: dict[str, Any]) -> list[dict[str, Any]]:
    items = [item for item in list_value(scene.get("dialogue_items")) if isinstance(item, dict)]
    if not items:
        items = [item for item in list_value(scene.get("dialogues")) if isinstance(item, dict)]
    return sorted(items, key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end"))))


def scene_times(scene: dict[str, Any], dialogues: list[dict[str, Any]]) -> tuple[float, float, float]:
    if dialogues:
        start = safe_float(scene.get("start"), safe_float(dialogues[0].get("start")))
        end = safe_float(scene.get("end"), safe_float(dialogues[-1].get("end")))
    else:
        start = safe_float(scene.get("start"))
        end = safe_float(scene.get("end"))
    duration = safe_float(scene.get("duration"), end - start)
    if duration <= 0 and end >= start:
        duration = end - start
    return start, end, duration


def validate_dialogue_times(dialogues: list[dict[str, Any]]) -> None:
    previous_start = None
    for index, dialogue in enumerate(dialogues):
        start = safe_float(dialogue.get("start"))
        end = safe_float(dialogue.get("end"))
        if end < start:
            raise BlockedError("dialogue_time_invalid", f"Dialogue time is invalid at index {index}: end < start.")
        if previous_start is not None and start < previous_start:
            raise BlockedError("dialogue_order_invalid", f"Dialogue order is invalid at index {index}: start moved backwards.")
        previous_start = start


def first_nonempty_path(items: list[Any]) -> str:
    for item in items:
        if isinstance(item, str) and item.strip():
            return item.strip()
        if isinstance(item, dict):
            path = text_value(item.get("path") or item.get("image_path"))
            if path:
                return path
    return ""


def image_slots(dialogue: dict[str, Any], scene: dict[str, Any], dialogue_index: int) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for owner in (dict_value(dialogue.get("working_assets")),):
        for image in list_value(owner.get("images")):
            if isinstance(image, dict):
                slots.append(image)
    # Backward compatibility for early scene-level StoryBoard assets. Only use
    # scene-level images for the first dialogue in the scene.
    if not slots and dialogue_index == 0:
        scene_assets = dict_value(scene.get("working_assets"))
        for image in list_value(scene_assets.get("images")):
            if isinstance(image, dict):
                slots.append(image)
    return slots


def audio_slot(dialogue: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any]:
    audio = dict_value(dict_value(dialogue.get("working_assets")).get("audio"))
    if audio:
        return audio
    return dict_value(dict_value(scene.get("working_assets")).get("audio"))


def video_slot(dialogue: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any]:
    video = dict_value(dict_value(dialogue.get("working_assets")).get("video"))
    if video:
        return video
    return dict_value(dict_value(scene.get("working_assets")).get("video"))


def bound_video_slot(dialogue: dict[str, Any], scene: dict[str, Any], dialogue_index: int) -> dict[str, Any]:
    video = dict_value(dict_value(dialogue.get("working_assets")).get("video"))
    if video:
        return video
    # Backward compatibility for early scene-level StoryBoard video assets.
    # Avoid treating one scene-level video as an anchor for every dialogue.
    if dialogue_index == 0:
        return dict_value(dict_value(scene.get("working_assets")).get("video"))
    return {}


def is_talking_head_dialogue(dialogue: dict[str, Any]) -> bool:
    video_plan = dict_value(dialogue.get("video_plan"))
    return video_plan.get("is_talking_head") is not False


def lipsync_plan_for_segment(ref: SceneRef, dialogues: list[dict[str, Any]], start_index: int, end_index: int) -> dict[str, Any]:
    del ref, end_index
    first_dialogue = dialogues[start_index] if 0 <= start_index < len(dialogues) else {}
    video_plan = dict_value(first_dialogue.get("video_plan"))
    if video_plan.get("is_talking_head") is False:
        return {
            "need_lipsync": False,
            "lipsync_disabled_by_ui": True,
            "lipsync_reason": "user_marked_cutaway",
            "lipsync_decision_source": "dialogue.video_plan.is_talking_head",
        }
    if video_plan.get("is_talking_head") is True:
        return {
            "need_lipsync": True,
            "lipsync_disabled_by_ui": False,
            "lipsync_reason": "dialogue_marked_talking_head",
            "lipsync_decision_source": "dialogue.video_plan.is_talking_head",
        }
    return {
        "need_lipsync": True,
        "lipsync_disabled_by_ui": False,
        "lipsync_reason": "visible_face",
        "lipsync_decision_source": "default",
    }


def is_working_path(path: str) -> bool:
    return path.startswith(f"{STORYBOARD_WORKING_DIR_REL}/")


def infer_placed_source_type(path: str, slot: dict[str, Any]) -> str:
    value = text_value(slot.get("source_type") or slot.get("source"))
    if value:
        return value
    if path.startswith("SessionOutput/storyboard/assets/") or path.startswith("SessionOutput/visual/"):
        return "placed_uploaded_image"
    return "generated_image"


def is_new_image_slot(slot: dict[str, Any], path: str) -> bool:
    slot_name = text_value(slot.get("slot"))
    return slot_name == "Image_New" or bool(re.search(r"_Image_New\.[^/]+$", path))


def new_image_visual(workspace: Path, dialogue: dict[str, Any], scene: dict[str, Any], dialogue_index: int) -> VisualSource | None:
    asset_key = dialogue_key(dialogue, dialogue_index)
    planned_path = working_path_for(asset_key, "Image_New.png")
    for slot in image_slots(dialogue, scene, dialogue_index):
        path = text_value(slot.get("path"))
        if not path:
            continue
        if not is_new_image_slot(slot, path):
            continue
        source_type = infer_placed_source_type(path, slot)
        if is_working_path(path):
            return VisualSource(
                source_type="generated_image",
                source_path=path,
                requires_generated_image_before_video=False,
                planned_generated_image_path=path,
                materialize_first_frame={"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""},
                existing_video={"path": "", "materialize_video": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""}},
                need_image_prompt=False,
                need_image=False,
                need_video_prompt=True,
                need_video=True,
            )
        return VisualSource(
            source_type="placed_uploaded_image",
            source_path=path,
            requires_generated_image_before_video=False,
            planned_generated_image_path=planned_path,
            materialize_first_frame={
                "required": True,
                "copy_from_path": path,
                "copy_to_path": planned_path,
                "source_type": source_type or "placed_uploaded_image",
            },
            existing_video={"path": "", "materialize_video": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""}},
            need_image_prompt=False,
            need_image=False,
            need_video_prompt=True,
            need_video=True,
        )
    return None


def bound_video_visual(workspace: Path, dialogue: dict[str, Any], scene: dict[str, Any], dialogue_index: int) -> VisualSource | None:
    slot = bound_video_slot(dialogue, scene, dialogue_index)
    path = text_value(slot.get("path"))
    if not path:
        return None
    if not file_exists(workspace, path):
        return None
    asset_key = dialogue_key(dialogue, dialogue_index)
    planned_path = working_path_for(asset_key, "Video_Final.mp4")
    source_type = text_value(slot.get("source_type") or slot.get("source")) or "bound_dialogue_video"
    return VisualSource(
        source_type="bound_video",
        source_path=path,
        requires_generated_image_before_video=False,
        planned_generated_image_path="",
        materialize_first_frame={"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""},
        existing_video={
            "path": path,
            "materialize_video": {
                "required": path != planned_path,
                "copy_from_path": path,
                "copy_to_path": planned_path,
                "source_type": source_type,
            },
        },
        need_image_prompt=False,
        need_image=False,
        need_video_prompt=False,
        need_video=False,
    )


def existing_raw_video_visual(workspace: Path, dialogue: dict[str, Any], dialogue_index: int) -> VisualSource | None:
    asset_key = dialogue_key(dialogue, dialogue_index)
    raw_path = existing_working_slot_path(workspace, asset_key, "Video_Raw", VIDEO_EXTS)
    if not raw_path:
        return None
    return VisualSource(
        source_type="existing_raw_video",
        source_path=raw_path,
        requires_generated_image_before_video=False,
        planned_generated_image_path="",
        materialize_first_frame={"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""},
        existing_video={"path": "", "materialize_video": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""}},
        need_image_prompt=False,
        need_image=False,
        need_video_prompt=False,
        need_video=False,
    )


def old_image_visual(workspace: Path, dialogue: dict[str, Any], scene: dict[str, Any], dialogue_index: int) -> VisualSource | None:
    path = text_value(dialogue.get("image_path"))
    if not path:
        path = first_nonempty_path(list_value(dialogue.get("key_frame_paths")))
    if not path:
        return None
    asset_key = dialogue_key(dialogue, dialogue_index)
    planned_path = working_path_for(asset_key, "Image_New.png")
    prompt_path = existing_working_slot_path(workspace, asset_key, "ImagePrompt", JSON_EXTS)
    return VisualSource(
        source_type="original_image",
        source_path=path,
        requires_generated_image_before_video=True,
        planned_generated_image_path=planned_path,
        materialize_first_frame={"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""},
        existing_video={"path": "", "materialize_video": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""}},
        need_image_prompt=not bool(prompt_path),
        need_image=True,
        need_video_prompt=True,
        need_video=True,
        image_prompt_path=prompt_path,
    )


def existing_image_prompt_visual(workspace: Path, dialogue: dict[str, Any], dialogue_index: int) -> VisualSource | None:
    asset_key = dialogue_key(dialogue, dialogue_index)
    prompt_path = existing_working_slot_path(workspace, asset_key, "ImagePrompt", JSON_EXTS)
    if not prompt_path:
        return None
    return VisualSource(
        source_type="existing_image_prompt",
        source_path="",
        requires_generated_image_before_video=True,
        planned_generated_image_path=working_path_for(asset_key, "Image_New.png"),
        materialize_first_frame={"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""},
        existing_video={"path": "", "materialize_video": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""}},
        need_image_prompt=False,
        need_image=True,
        need_video_prompt=True,
        need_video=True,
        image_prompt_path=prompt_path,
    )


def dialogue_has_dance_mimic_reference(dialogue: dict[str, Any]) -> bool:
    nested = dict_value(dialogue.get("dance_mimic"))
    return bool(
        text_value(nested.get("reference_video_path") or dialogue.get("reference_video_path"))
        or text_value(nested.get("reference_video_role") or dialogue.get("reference_video_role")) == DANCE_MIMIC_REFERENCE_VIDEO_ROLE
    )


def visual_for_dialogue(workspace: Path, dialogue: dict[str, Any], scene: dict[str, Any], dialogue_index: int) -> VisualSource | None:
    return (
        bound_video_visual(workspace, dialogue, scene, dialogue_index)
        or existing_raw_video_visual(workspace, dialogue, dialogue_index)
        or new_image_visual(workspace, dialogue, scene, dialogue_index)
        or old_image_visual(workspace, dialogue, scene, dialogue_index)
        or existing_image_prompt_visual(workspace, dialogue, dialogue_index)
    )


def tail_path_for_video(video_path: str) -> str:
    if video_path.endswith("_Video_Final.mp4"):
        return video_path[:-len("_Video_Final.mp4")] + "_TailFrame.png"
    if video_path.endswith(".mp4"):
        return video_path[:-4] + "_TailFrame.png"
    return ""


def existing_tail_for_scene(workspace: Path, ref: SceneRef | None) -> TailSource | None:
    if ref is None:
        return None
    dialogues = scene_dialogues(ref.scene)
    for index in range(len(dialogues) - 1, -1, -1):
        dialogue = dialogues[index]
        video_path = text_value(video_slot(dialogue, ref.scene).get("path"))
        if not video_path:
            asset_key = dialogue_key(dialogue, index)
            candidate = working_path_for(asset_key, "Video_Final.mp4")
            if file_exists(workspace, candidate):
                video_path = candidate
        if not video_path:
            continue
        tail_path = tail_path_for_video(video_path)
        if tail_path and file_exists(workspace, tail_path):
            return TailSource(
                source_type="previous_scene_tail_frame",
                source_path=tail_path,
                depends_on_segment_id="",
                depends_on_video_path=video_path,
                depends_on_tail_frame_path=tail_path,
                available=True,
                continuation_allowed=is_talking_head_dialogue(dialogue),
            )
    return None


def planned_tail_for_segment(segment: dict[str, Any]) -> TailSource:
    tail_path = text_value(dict_value(segment.get("tail_frame")).get("planned_path"))
    continuation_allowed = dict_value(segment.get("tail_frame")).get("continuation_allowed")
    video_path = text_value(dict_value(segment.get("planned_outputs")).get("video_path"))
    return TailSource(
        source_type="previous_segment_tail_frame",
        source_path=tail_path,
        depends_on_segment_id=text_value(segment.get("segment_id")),
        depends_on_video_path=video_path,
        depends_on_tail_frame_path=tail_path,
        available=bool(tail_path),
        continuation_allowed=continuation_allowed is not False,
    )


def visual_from_tail(tail: TailSource, asset_key: str = "") -> VisualSource:
    planned_path = working_path_for(asset_key, "Image_New.png") if asset_key else ""
    return VisualSource(
        source_type=tail.source_type,
        source_path=tail.source_path,
        requires_generated_image_before_video=False,
        planned_generated_image_path=planned_path,
        materialize_first_frame={
            "required": bool(planned_path),
            "copy_from_path": tail.source_path if planned_path else "",
            "copy_to_path": planned_path,
            "source_type": tail.source_type,
        },
        existing_video={"path": "", "materialize_video": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""}},
        need_image_prompt=False,
        need_image=False,
        need_video_prompt=True,
        need_video=True,
    )


def segment_ranges_for_dialogues(dialogues: list[dict[str, Any]], start_index: int, end_index: int, max_seconds: float, split_tolerance_seconds: float) -> list[tuple[int, int, bool]]:
    ranges: list[tuple[int, int, bool]] = []
    cursor = start_index
    segment_limit = min(float(max_seconds) + max(0.0, float(split_tolerance_seconds)), VIDEO_MODEL_MAX_SECONDS)
    while cursor <= end_index:
        start_time = safe_float(dialogues[cursor].get("start"))
        single_duration = safe_float(dialogues[cursor].get("duration"), safe_float(dialogues[cursor].get("end")) - start_time)
        if single_duration > segment_limit:
            ranges.append((cursor, cursor, True))
            cursor += 1
            continue
        best_end = cursor
        best_delta = abs((safe_float(dialogues[cursor].get("end")) - start_time) - max_seconds)
        for candidate in range(cursor + 1, end_index + 1):
            duration = safe_float(dialogues[candidate].get("end")) - start_time
            if duration > segment_limit:
                break
            delta = abs(duration - max_seconds)
            if delta <= best_delta:
                best_delta = delta
                best_end = candidate
            if duration >= max_seconds and delta > best_delta:
                break
        ranges.append((cursor, best_end, False))
        cursor = best_end + 1
    return ranges


def segment_status_from_tasks(tasks: dict[str, bool]) -> str:
    return "planned" if tasks.get("need_video") or tasks.get("need_sync") or tasks.get("need_audio_video_sync") else "ready"


def build_segment(
    ref: SceneRef,
    dialogues: list[dict[str, Any]],
    start_index: int,
    end_index: int,
    segment_index: int,
    visual: VisualSource,
    tail_dependency: TailSource | None,
    duration_exceeds_limit_unavoidable: bool,
    min_video_seconds: float,
) -> dict[str, Any]:
    first_dialogue = dialogues[start_index]
    asset_key = dialogue_key(first_dialogue, start_index)
    if visual.source_type in {"previous_segment_tail_frame", "previous_scene_tail_frame"}:
        visual = visual_from_tail(
            TailSource(
                source_type=visual.source_type,
                source_path=visual.source_path,
                depends_on_segment_id=tail_dependency.depends_on_segment_id if tail_dependency else "",
                depends_on_video_path=tail_dependency.depends_on_video_path if tail_dependency else "",
                depends_on_tail_frame_path=tail_dependency.depends_on_tail_frame_path if tail_dependency else visual.source_path,
                available=tail_dependency.available if tail_dependency else True,
                continuation_allowed=tail_dependency.continuation_allowed if tail_dependency else True,
            ),
            asset_key,
        )
    is_dance_mimic_reference_video = dialogue_has_dance_mimic_reference(first_dialogue)
    start = safe_float(dialogues[start_index].get("start"))
    end = safe_float(dialogues[end_index].get("end"))
    duration = end - start
    planned_video_duration = max(duration, float(min_video_seconds))
    dialogue_asset_keys = [dialogue_key(item, start_index + offset) for offset, item in enumerate(dialogues[start_index:end_index + 1])]
    audio_tasks = []
    for offset, dialogue in enumerate(dialogues[start_index:end_index + 1], start=start_index):
        key = dialogue_key(dialogue, offset)
        audio_path = text_value(audio_slot(dialogue, ref.scene).get("path"))
        need_audio = not bool(audio_path)
        audio_tasks.append({
            "srt_id": text_value(dialogue.get("srt_id") or dialogue.get("dialogue_id")),
            "dialogue_asset_key": key,
            "need_audio": need_audio,
            "audio_source": "existing_dialogue_audio" if audio_path else "to_generate",
            "existing_audio_path": audio_path,
            "planned_audio_path": audio_path or working_path_for(key, "Audio_Final.wav"),
        })

    if is_dance_mimic_reference_video:
        audio_tasks = [item for item in audio_tasks if text_value(item.get("existing_audio_path"))]
    need_audio = False if is_dance_mimic_reference_video else any(item["need_audio"] for item in audio_tasks)
    continuation_allowed = True if is_dance_mimic_reference_video else is_talking_head_dialogue(first_dialogue)
    lipsync = lipsync_plan_for_segment(ref, dialogues, start_index, end_index)
    if visual.source_type == "bound_video":
        lipsync = {
            "need_lipsync": False,
            "lipsync_disabled_by_ui": False,
            "lipsync_reason": "existing_video_bound_complete",
            "lipsync_decision_source": "bound_dialogue_video",
        }
    if is_dance_mimic_reference_video:
        lipsync = {
            "need_lipsync": False,
            "lipsync_disabled_by_ui": True,
            "lipsync_reason": "dance_mimic_reference_video",
            "lipsync_decision_source": DANCE_MIMIC_WORKFLOW_ID,
        }
    need_lipsync = bool(lipsync.get("need_lipsync", True))
    need_audio_video_sync = True if is_dance_mimic_reference_video else not need_lipsync
    image_prompt_path = visual.image_prompt_path or working_path_for(asset_key, "ImagePrompt.json")
    raw_video_path = visual.source_path if visual.source_type == "existing_raw_video" else working_path_for(asset_key, "Video_Raw.mp4")
    tasks = {
        "need_audio": need_audio,
        "need_image_prompt": bool(visual.need_image_prompt),
        "need_image": bool(visual.need_image),
        "need_video_prompt": bool(visual.need_video_prompt),
        "need_video": bool(visual.need_video),
        "need_lipsync": need_lipsync,
        "need_audio_video_sync": need_audio_video_sync,
        "need_sync": True,
        "sync_mode": "lipsync" if need_lipsync else "audio_replace_retime",
        "lipsync_disabled_by_ui": bool(lipsync.get("lipsync_disabled_by_ui", False)),
        "lipsync_reason": text_value(lipsync.get("lipsync_reason")) or "visible_face",
        "lipsync_decision_source": text_value(lipsync.get("lipsync_decision_source")) or "default",
    }
    return {
        "segment_id": f"{text_value(ref.shot.get('shot_id'))}_{text_value(ref.scene.get('scene_id'))}_segment_{segment_index:03d}",
        "asset_key": asset_key,
        "segment_index": segment_index,
        "status": segment_status_from_tasks(tasks),
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(end - start, 3),
        "planned_video_duration": round(planned_video_duration, 3),
        "duration_padding_seconds": round(max(0.0, planned_video_duration - duration), 3),
        "duration_policy": "model_minimum_extended" if planned_video_duration > duration else "match_dialogue_timeline",
        "duration_exceeds_limit_unavoidable": bool(duration_exceeds_limit_unavoidable),
        "dialogue_asset_keys": dialogue_asset_keys,
        "dialogue_ids": dialogue_asset_keys,
        "talking_head_reference": dict_value(first_dialogue.get("talking_head_reference")),
        "dependencies": {
            "depends_on_segment_id": tail_dependency.depends_on_segment_id if tail_dependency else "",
            "depends_on_video_path": tail_dependency.depends_on_video_path if tail_dependency else "",
            "depends_on_tail_frame_path": tail_dependency.depends_on_tail_frame_path if tail_dependency else "",
        },
        "first_frame": {
            "source_type": visual.source_type,
            "source_path": visual.source_path,
            "requires_generated_image_before_video": visual.requires_generated_image_before_video,
            "planned_generated_image_path": visual.planned_generated_image_path,
            "materialize_first_frame": visual.materialize_first_frame,
        },
        "tail_frame": {
            "planned_path": working_path_for(asset_key, "TailFrame.png"),
            "available": False,
            "continuation_allowed": continuation_allowed,
            "continuation_policy_source": DANCE_MIMIC_WORKFLOW_ID if is_dance_mimic_reference_video else "dialogue.video_plan.is_talking_head",
        },
        "tasks": tasks,
        "existing_video": visual.existing_video,
        "dialogue_audio_tasks": audio_tasks,
        "planned_outputs": {
            "image_prompt_path": image_prompt_path if tasks["need_image_prompt"] or tasks["need_image"] else "",
            "image_path": visual.planned_generated_image_path,
            "segment_audio_path": working_path_for(asset_key, "SegmentAudio_Final.wav"),
            "video_prompt_path": working_path_for(asset_key, "VideoPrompt.json") if tasks["need_video_prompt"] else "",
            "raw_video_path": raw_video_path,
            "final_video_path": working_path_for(asset_key, "Video_Final.mp4"),
            "video_path": working_path_for(asset_key, "Video_Final.mp4"),
            "video_duration_seconds": round(planned_video_duration, 3),
        },
        "blocked_reason": "",
    }


def blocked_segment_payload(
    ref: SceneRef,
    dialogues: list[dict[str, Any]],
    start_index: int,
    end_index: int,
    segment_index: int,
    code: str,
    message: str,
) -> dict[str, Any]:
    start = safe_float(dialogues[start_index].get("start"))
    end = safe_float(dialogues[end_index].get("end"))
    asset_key = dialogue_key(dialogues[start_index], start_index)
    return {
        "segment_id": f"{text_value(ref.shot.get('shot_id'))}_{text_value(ref.scene.get('scene_id'))}_segment_{segment_index:03d}",
        "asset_key": asset_key,
        "segment_index": segment_index,
        "status": "blocked",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(end - start, 3),
        "planned_video_duration": 0,
        "duration_padding_seconds": 0,
        "duration_policy": "blocked",
        "duration_exceeds_limit_unavoidable": False,
        "dialogue_asset_keys": [dialogue_key(item, start_index + offset) for offset, item in enumerate(dialogues[start_index:end_index + 1])],
        "dialogue_ids": [dialogue_key(item, start_index + offset) for offset, item in enumerate(dialogues[start_index:end_index + 1])],
        "dependencies": {"depends_on_segment_id": "", "depends_on_video_path": "", "depends_on_tail_frame_path": ""},
        "first_frame": {"source_type": "missing", "source_path": "", "requires_generated_image_before_video": False, "planned_generated_image_path": "", "materialize_first_frame": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""}},
        "tail_frame": {"planned_path": "", "available": False, "continuation_allowed": False, "continuation_policy_source": ""},
        "tasks": {"need_audio": False, "need_image_prompt": False, "need_image": False, "need_video_prompt": False, "need_video": False, "need_lipsync": False, "need_audio_video_sync": False, "need_sync": False, "sync_mode": "", "lipsync_disabled_by_ui": False, "lipsync_reason": "", "lipsync_decision_source": ""},
        "existing_video": {"path": "", "materialize_video": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""}},
        "dialogue_audio_tasks": [],
        "planned_outputs": {"image_prompt_path": "", "image_path": "", "segment_audio_path": "", "video_prompt_path": "", "raw_video_path": "", "final_video_path": "", "video_path": "", "video_duration_seconds": 0},
        "blocked_reason": {"code": code, "message": message},
    }


def visible_non_executable_segment_payload(
    ref: SceneRef,
    dialogues: list[dict[str, Any]],
    *,
    status: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    start_index = 0
    end_index = max(0, len(dialogues) - 1)
    start = safe_float(dialogues[start_index].get("start")) if dialogues else safe_float(ref.scene.get("start"))
    end = safe_float(dialogues[end_index].get("end")) if dialogues else safe_float(ref.scene.get("end"), start)
    asset_key = dialogue_key(dialogues[start_index], start_index) if dialogues else sanitize_asset_key(text_value(ref.scene.get("scene_id")), "scene")
    is_dance_mimic_reference_video = bool(dialogues and dialogue_has_dance_mimic_reference(dialogues[start_index]))
    audio_tasks = []
    if not is_dance_mimic_reference_video:
        for offset, dialogue in enumerate(dialogues):
            key = dialogue_key(dialogue, offset)
            audio_path = text_value(audio_slot(dialogue, ref.scene).get("path"))
            audio_tasks.append({
                "srt_id": text_value(dialogue.get("srt_id") or dialogue.get("dialogue_id")),
                "dialogue_asset_key": key,
                "need_audio": not bool(audio_path),
                "audio_source": "existing_dialogue_audio" if audio_path else "to_generate",
                "existing_audio_path": audio_path,
                "planned_audio_path": audio_path or working_path_for(key, "Audio_Final.wav"),
            })
    reason = {"code": code, "message": message}
    return {
        "segment_id": f"{text_value(ref.shot.get('shot_id'))}_{text_value(ref.scene.get('scene_id'))}_segment_001",
        "asset_key": asset_key,
        "segment_index": 1,
        "status": status,
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(max(0.0, end - start), 3),
        "planned_video_duration": 0,
        "duration_padding_seconds": 0,
        "duration_policy": status,
        "duration_exceeds_limit_unavoidable": False,
        "dialogue_asset_keys": [dialogue_key(item, offset) for offset, item in enumerate(dialogues)],
        "dialogue_ids": [dialogue_key(item, offset) for offset, item in enumerate(dialogues)],
        "dependencies": {"depends_on_segment_id": "", "depends_on_video_path": "", "depends_on_tail_frame_path": ""},
        "first_frame": {"source_type": "missing", "source_path": "", "requires_generated_image_before_video": False, "planned_generated_image_path": "", "materialize_first_frame": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""}},
        "tail_frame": {"planned_path": "", "available": False, "continuation_allowed": False, "continuation_policy_source": ""},
        "tasks": {"need_audio": any(item["need_audio"] for item in audio_tasks), "need_image_prompt": False, "need_image": False, "need_video_prompt": False, "need_video": False, "need_lipsync": False, "need_audio_video_sync": False, "need_sync": False, "sync_mode": "", "lipsync_disabled_by_ui": False, "lipsync_reason": "", "lipsync_decision_source": ""},
        "existing_video": {"path": "", "materialize_video": {"required": False, "copy_from_path": "", "copy_to_path": "", "source_type": ""}},
        "dialogue_audio_tasks": audio_tasks,
        "planned_outputs": {"image_prompt_path": "", "image_path": "", "segment_audio_path": working_path_for(asset_key, "SegmentAudio_Final.wav"), "video_prompt_path": "", "raw_video_path": "", "final_video_path": "", "video_path": "", "video_duration_seconds": 0},
        "blocked_reason": reason if status == "blocked" else "",
        "skipped_reason": reason if status == "skipped" else "",
    }


def append_segments_for_range(
    ref: SceneRef,
    dialogues: list[dict[str, Any]],
    start_index: int,
    end_index: int,
    first_visual: VisualSource,
    segments: list[dict[str, Any]],
    max_seconds: float,
    min_video_seconds: float,
    split_tolerance_seconds: float,
    initial_tail_dependency: TailSource | None = None,
) -> None:
    if first_visual.source_type == "bound_video":
        ranges = segment_ranges_for_dialogues(dialogues, start_index, start_index, max_seconds, split_tolerance_seconds)
        if start_index < end_index:
            ranges.extend(segment_ranges_for_dialogues(dialogues, start_index + 1, end_index, max_seconds, split_tolerance_seconds))
    else:
        ranges = segment_ranges_for_dialogues(dialogues, start_index, end_index, max_seconds, split_tolerance_seconds)
    tail_dependency: TailSource | None = initial_tail_dependency
    visual = first_visual
    for local_start, local_end, exceeds in ranges:
        if visual.source_type == "previous_segment_tail_frame" and tail_dependency is not None and not tail_dependency.continuation_allowed:
            segments.append(blocked_segment_payload(
                ref,
                dialogues,
                local_start,
                end_index,
                len(segments) + 1,
                "previous_segment_cutaway_tail_not_usable",
                "Previous segment is marked as cutaway, so its tail frame cannot drive empty dialogues.",
            ))
            break
        segment = build_segment(
            ref=ref,
            dialogues=dialogues,
            start_index=local_start,
            end_index=local_end,
            segment_index=len(segments) + 1,
            visual=visual,
            tail_dependency=tail_dependency,
            duration_exceeds_limit_unavoidable=exceeds,
            min_video_seconds=min_video_seconds,
        )
        segments.append(segment)
        tail_dependency = planned_tail_for_segment(segment)
        visual = visual_from_tail(tail_dependency)


def anchor_indices(workspace: Path, scene: dict[str, Any], dialogues: list[dict[str, Any]]) -> list[int]:
    indices: list[int] = []
    for index, dialogue in enumerate(dialogues):
        if visual_for_dialogue(workspace, dialogue, scene, index):
            indices.append(index)
    return indices


def previous_global_ref(refs: list[SceneRef], ref: SceneRef) -> SceneRef | None:
    if ref.global_index <= 0:
        return None
    return refs[ref.global_index - 1]


def scene_tail_for_start(
    workspace: Path,
    args: Args,
    refs: list[SceneRef],
    ref: SceneRef,
    last_planned_segment: dict[str, Any] | None,
) -> TailSource | None:
    if last_planned_segment is not None:
        return planned_tail_for_segment(last_planned_segment)
    return existing_tail_for_scene(workspace, previous_global_ref(refs, ref))


def blocked_scene_payload(ref: SceneRef, dialogues: list[dict[str, Any]], code: str, message: str) -> dict[str, Any]:
    start, end, duration = scene_times(ref.scene, dialogues)
    segments = [visible_non_executable_segment_payload(ref, dialogues, status="blocked", code=code, message=message)] if dialogues else []
    return {
        "scene_id": text_value(ref.scene.get("scene_id")),
        "status": "blocked",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(duration, 3),
        "segments": segments,
        "blocked_reason": {"code": code, "message": message},
        "skipped_reason": "",
    }


def skipped_scene_payload(ref: SceneRef, dialogues: list[dict[str, Any]], code: str, message: str) -> dict[str, Any]:
    start, end, duration = scene_times(ref.scene, dialogues)
    segments = [visible_non_executable_segment_payload(ref, dialogues, status="skipped", code=code, message=message)] if dialogues else []
    return {
        "scene_id": text_value(ref.scene.get("scene_id")),
        "status": "skipped",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(duration, 3),
        "segments": segments,
        "blocked_reason": "",
        "skipped_reason": {"code": code, "message": message},
    }


def plan_dance_mimic_scene(
    workspace: Path,
    args: Args,
    ref: SceneRef,
    dialogues: list[dict[str, Any]],
    last_planned_segment: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    start, end, duration = scene_times(ref.scene, dialogues)
    segments: list[dict[str, Any]] = []
    previous_segment: dict[str, Any] | None = last_planned_segment

    for index, dialogue in enumerate(dialogues):
        if index == 0:
            visual = visual_for_dialogue(workspace, dialogue, ref.scene, index)
            tail_dependency = None
            if visual is None:
                return blocked_scene_payload(
                    ref,
                    dialogues,
                    "dancemimic_first_frame_missing",
                    "DanceMimic requires a target identity image in the first Dialogue Image_New/source image binding; reference video cannot be used as the first frame.",
                ), last_planned_segment
        else:
            if previous_segment is None:
                return blocked_scene_payload(
                    ref,
                    dialogues,
                    "dancemimic_previous_segment_missing",
                    "DanceMimic continuation requires the previous segment before planning the next segment.",
                ), previous_segment
            tail_dependency = planned_tail_for_segment(previous_segment)
            if not text_value(tail_dependency.source_path):
                return blocked_scene_payload(
                    ref,
                    dialogues,
                    "dancemimic_previous_tail_path_missing",
                    "DanceMimic continuation requires a planned previous TailFrame path.",
                ), previous_segment
            visual = visual_from_tail(tail_dependency)

        segment = build_segment(
            ref=ref,
            dialogues=dialogues,
            start_index=index,
            end_index=index,
            segment_index=len(segments) + 1,
            visual=visual,
            tail_dependency=tail_dependency,
            duration_exceeds_limit_unavoidable=safe_float(dialogue.get("end")) - safe_float(dialogue.get("start")) > float(args.max_video_seconds),
            min_video_seconds=args.min_video_seconds,
        )
        segments.append(segment)
        previous_segment = segment if segment.get("status") != "blocked" else previous_segment

    if not segments:
        return blocked_scene_payload(ref, dialogues, "scene_segments_empty", "DanceMimic scene did not produce any video segments."), last_planned_segment
    return {
        "scene_id": text_value(ref.scene.get("scene_id")),
        "status": "planned" if any(segment.get("status") != "blocked" for segment in segments) else "blocked",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(duration, 3),
        "segments": segments,
        "blocked_reason": "",
        "skipped_reason": "",
    }, previous_segment


def plan_scene(
    workspace: Path,
    args: Args,
    refs: list[SceneRef],
    ref: SceneRef,
    last_planned_segment: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    dialogues = scene_dialogues(ref.scene)
    if not dialogues:
        return blocked_scene_payload(ref, dialogues, "scene_dialogues_empty", "Scene contains no dialogue items."), last_planned_segment
    validate_dialogue_times(dialogues)
    for index, dialogue in enumerate(dialogues):
        if not text_value(dialogue.get("dialogue_asset_key")):
            raise BlockedError("dialogue_asset_key_missing", f"Dialogue at index {index} has no dialogue_asset_key.")

    if all(dialogue_has_dance_mimic_reference(dialogue) for dialogue in dialogues):
        return plan_dance_mimic_scene(workspace, args, ref, dialogues, last_planned_segment)

    first_visual = visual_for_dialogue(workspace, dialogues[0], ref.scene, 0)
    anchors = anchor_indices(workspace, ref.scene, dialogues)
    if ref.global_index == 0 and first_visual is None:
        if dialogue_has_dance_mimic_reference(dialogues[0]):
            return blocked_scene_payload(
                ref,
                dialogues,
                "dancemimic_first_frame_missing",
                "DanceMimic requires a target identity image in the current Dialogue Image_New/Image_Source slot; reference video cannot be used as the first frame.",
            ), last_planned_segment
        return skipped_scene_payload(
            ref,
            dialogues,
            "first_scene_missing_visual_source",
            "The first scene has no visual source at its first dialogue, so no video can be generated for it.",
        ), last_planned_segment

    segments: list[dict[str, Any]] = []
    start, end, duration = scene_times(ref.scene, dialogues)

    if first_visual is None:
        tail = scene_tail_for_start(workspace, args, refs, ref, last_planned_segment)
        if tail is None or (args.target_type == "scene" and not file_exists(workspace, tail.source_path)):
            return blocked_scene_payload(
                ref,
                dialogues,
                "scene_first_dialogue_missing_first_frame_and_previous_tail_missing",
                "Scene starts without a visual source and no usable previous tail frame is available.",
            ), last_planned_segment
        if not tail.continuation_allowed:
            return blocked_scene_payload(
                ref,
                dialogues,
                "previous_segment_cutaway_tail_not_usable",
                "Scene starts without a visual source and the previous segment is marked as cutaway, so its tail frame cannot be reused.",
            ), last_planned_segment
        first_anchor = anchors[0] if anchors else len(dialogues)
        if first_anchor > 0:
            append_segments_for_range(
                ref,
                dialogues,
                0,
                first_anchor - 1,
                visual_from_tail(tail),
                segments,
                args.max_video_seconds,
                args.min_video_seconds,
                args.split_tolerance_seconds,
                initial_tail_dependency=tail,
            )

    if anchors:
        for anchor_pos, anchor_index in enumerate(anchors):
            next_anchor = anchors[anchor_pos + 1] if anchor_pos + 1 < len(anchors) else len(dialogues)
            visual = visual_for_dialogue(workspace, dialogues[anchor_index], ref.scene, anchor_index)
            if visual is None:
                continue
            append_segments_for_range(ref, dialogues, anchor_index, next_anchor - 1, visual, segments, args.max_video_seconds, args.min_video_seconds, args.split_tolerance_seconds)
    elif first_visual is not None:
        append_segments_for_range(ref, dialogues, 0, len(dialogues) - 1, first_visual, segments, args.max_video_seconds, args.min_video_seconds, args.split_tolerance_seconds)

    scene_status = "planned" if any(segment.get("status") != "blocked" for segment in segments) else "blocked"
    if not segments:
        return blocked_scene_payload(ref, dialogues, "scene_segments_empty", "Scene did not produce any video segments."), last_planned_segment

    last_tail_segment = next((segment for segment in reversed(segments) if segment.get("status") != "blocked"), None)
    return {
        "scene_id": text_value(ref.scene.get("scene_id")),
        "status": scene_status,
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(duration, 3),
        "segments": segments,
        "blocked_reason": "",
        "skipped_reason": "",
    }, last_tail_segment


def target_payload(args: Args) -> dict[str, Any]:
    return {"target_type": args.target_type, "shot_id": args.shot_id, "scene_id": args.scene_id}


def params_payload(args: Args) -> dict[str, Any]:
    return {
        "target_type": args.target_type,
        "shot_id": args.shot_id,
        "scene_id": args.scene_id,
        "max_video_seconds": float(args.max_video_seconds),
        "min_video_seconds": float(args.min_video_seconds),
        "split_tolerance_seconds": float(args.split_tolerance_seconds),
    }


def prepare_inputs(workspace: Path, variables: dict[str, Any], storyboard: dict[str, Any], args: Args, result: dict[str, Any]) -> dict[str, Any]:
    ensure_tool_dirs(workspace)
    prepared = result.setdefault("prepared_directories", [])
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        f"{TOOL_DIR_NAME}/Report",
    ):
        prepared.append(rel)
    params = params_payload(args)
    write_json(workspace / WORKING_VARIABLES_REL, variables)
    write_json(workspace / WORKING_STORYBOARD_REL, storyboard)
    write_json(workspace / WORKING_PARAMS_REL, params)
    state = {
        "tool": TOOL_NAME,
        "status": "ready",
        "phase": "prepare",
        "input_hashes": {
            "variables": stable_hash(variables),
            "storyboard": stable_hash(storyboard),
            "params": stable_hash(params),
        },
        "inputs": {
            "variables": WORKING_VARIABLES_REL,
            "storyboard": WORKING_STORYBOARD_REL,
            "params": WORKING_PARAMS_REL,
        },
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["inputs"] = state["inputs"]
    return state


def load_reusable_plan(workspace: Path, state: dict[str, Any], force: bool) -> dict[str, Any] | None:
    if force:
        return None
    output = workspace / OUTPUT_PLAN_REL
    existing_state_path = workspace / WORKING_STATE_REL
    if not output.exists() or not existing_state_path.exists():
        return None
    try:
        existing_state = read_json(existing_state_path)
        plan = read_json(output)
    except Exception:
        return None
    if existing_state.get("input_hashes") != state.get("input_hashes"):
        return None
    return plan if isinstance(plan, dict) else None


def build_plan(workspace: Path, args: Args, storyboard: dict[str, Any]) -> dict[str, Any]:
    refs = flatten_scenes(storyboard)
    if not refs:
        raise BlockedError("storyboard_scenes_empty", f"{SESSION_STORYBOARD_REL} contains no scenes.")
    selected = selected_scenes(refs, args)
    selected_ids = {(ref.shot_index, ref.scene_index) for ref in selected}

    shot_entries: dict[int, dict[str, Any]] = {}
    last_planned_segment: dict[str, Any] | None = None
    for ref in selected:
        scene_payload, maybe_tail_segment = plan_scene(workspace, args, refs, ref, last_planned_segment)
        if maybe_tail_segment is not None:
            last_planned_segment = maybe_tail_segment
        if ref.shot_index not in shot_entries:
            shot_entries[ref.shot_index] = {
                "shot_id": text_value(ref.shot.get("shot_id")),
                "status": "planned",
                "scenes": [],
            }
        shot_entries[ref.shot_index]["scenes"].append(scene_payload)

    shots = [shot_entries[index] for index in sorted(shot_entries)]
    for shot in shots:
        statuses = [scene.get("status") for scene in shot.get("scenes", [])]
        if any(status == "blocked" for status in statuses):
            shot["status"] = "blocked"
        elif any(status == "skipped" for status in statuses):
            shot["status"] = "completed_with_skipped_items"
        else:
            shot["status"] = "planned"

    dance_mimic_reference_video_segments = apply_dance_mimic_seed_to_shots(workspace, shots)
    summary = summarize_plan(shots)
    if dance_mimic_reference_video_segments:
        summary["dance_mimic_reference_video_segments"] = dance_mimic_reference_video_segments
    workflow_id = text_value(storyboard.get("workflow_id") or storyboard.get("profile_id"))
    consistency_references = (
        {
            "status": "not_required",
            "references": [],
            "missing": [],
            "blocking": False,
            "reason": "talking_head_portrait_first_frame",
        }
        if workflow_id == TALKING_HEAD_WORKFLOW_ID
        else consistency_reference_status(workspace)
    )
    plan = {
        "schema_version": "analysis_v1_video_generation_plan_0.1",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "source_storyboard_path": SESSION_STORYBOARD_REL,
        "target": target_payload(args),
        "settings": {
            "max_video_seconds": float(args.max_video_seconds),
            "min_video_seconds": float(args.min_video_seconds),
            "split_tolerance_seconds": float(args.split_tolerance_seconds),
        },
        "consistency_references": consistency_references,
        "summary": summary,
        "shots": shots,
        "created_at": now_iso(),
    }
    if dance_mimic_reference_video_segments:
        plan["workflow_id"] = DANCE_MIMIC_WORKFLOW_ID
        plan["storyboard_seed_path"] = STORYBOARD_SEED_REL
    elif workflow_id:
        plan["workflow_id"] = workflow_id
    return plan


def summarize_plan(shots: list[dict[str, Any]]) -> dict[str, Any]:
    scene_count = 0
    dialogue_asset_keys: set[str] = set()
    segment_count = 0
    blocked_segment_count = 0
    skipped_scene_count = 0
    blocked_scene_count = 0
    need_audio_count = 0
    need_image_prompt_count = 0
    need_image_count = 0
    need_video_prompt_count = 0
    need_video_count = 0
    segment_audio_count = 0
    need_lipsync_count = 0
    need_audio_video_sync_count = 0
    need_sync_count = 0
    for shot in shots:
        for scene in list_value(shot.get("scenes")):
            if not isinstance(scene, dict):
                continue
            scene_count += 1
            if scene.get("status") == "skipped":
                skipped_scene_count += 1
            if scene.get("status") == "blocked":
                blocked_scene_count += 1
            for segment in list_value(scene.get("segments")):
                if not isinstance(segment, dict):
                    continue
                segment_count += 1
                if segment.get("status") == "blocked":
                    blocked_segment_count += 1
                dialogue_asset_keys.update(text_value(item) for item in list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")) if text_value(item))
                tasks = dict_value(segment.get("tasks"))
                if tasks.get("need_audio"):
                    need_audio_count += sum(1 for item in list_value(segment.get("dialogue_audio_tasks")) if isinstance(item, dict) and item.get("need_audio"))
                if tasks.get("need_image_prompt"):
                    need_image_prompt_count += 1
                if tasks.get("need_image"):
                    need_image_count += 1
                if tasks.get("need_video_prompt"):
                    need_video_prompt_count += 1
                if tasks.get("need_video"):
                    need_video_count += 1
                if text_value(dict_value(segment.get("planned_outputs")).get("segment_audio_path")):
                    segment_audio_count += 1
                if tasks.get("need_lipsync"):
                    need_lipsync_count += 1
                if tasks.get("need_audio_video_sync"):
                    need_audio_video_sync_count += 1
                if tasks.get("need_sync") or tasks.get("need_lipsync") or tasks.get("need_audio_video_sync"):
                    need_sync_count += 1
    return {
        "shot_count": len(shots),
        "scene_count": scene_count,
        "dialogue_count": len(dialogue_asset_keys),
        "segment_count": segment_count,
        "blocked_segment_count": blocked_segment_count,
        "skipped_scene_count": skipped_scene_count,
        "blocked_scene_count": blocked_scene_count,
        "need_audio_count": need_audio_count,
        "need_image_prompt_count": need_image_prompt_count,
        "need_image_count": need_image_count,
        "need_video_prompt_count": need_video_prompt_count,
        "need_video_count": need_video_count,
        "segment_audio_count": segment_audio_count,
        "need_lipsync_count": need_lipsync_count,
        "need_audio_video_sync_count": need_audio_video_sync_count,
        "need_sync_count": need_sync_count,
    }


def status_from_plan(plan: dict[str, Any]) -> str:
    summary = dict_value(plan.get("summary"))
    if safe_float(summary.get("blocked_scene_count")) > 0 or safe_float(summary.get("blocked_segment_count")) > 0:
        return "completed_with_blocked_items"
    if safe_float(summary.get("skipped_scene_count")) > 0:
        return "completed_with_skipped_items"
    return "completed"


def finalize_outputs(workspace: Path, plan: dict[str, Any], state: dict[str, Any], result: dict[str, Any], reused: bool) -> None:
    plan["plan_hash"] = plan_hash(plan)
    write_json(workspace / OUTPUT_PLAN_REL, plan)
    write_json(workspace / SESSION_PLAN_REL, plan)
    state = {
        **state,
        "status": "completed",
        "phase": "finalize",
        "outputs": {
            "tool_output": OUTPUT_PLAN_REL,
            "session_output": SESSION_PLAN_REL,
        },
        "reused_completed_output": reused,
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["status"] = status_from_plan(plan)
    result["outputs"] = {"tool_output": OUTPUT_PLAN_REL, "session_output": SESSION_PLAN_REL}
    result["summary"] = plan.get("summary") or {}
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_STORYBOARD_REL,
        WORKING_PARAMS_REL,
        WORKING_STATE_REL,
        OUTPUT_PLAN_REL,
        SESSION_PLAN_REL,
        REPORT_RESULT_REL,
    ]
    if reused:
        result.setdefault("warnings", []).append({"code": "reused_completed_output", "message": "Reused completed video generation plan because input hashes matched."})


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    try:
        validate_workspace(workspace)
        validate_args(args)
        if args.force:
            force_reset(workspace, result)
        variables = load_variables(workspace)
        storyboard = load_storyboard(workspace)
        state = prepare_inputs(workspace, variables, storyboard, args, result)
        reusable = load_reusable_plan(workspace, state, force=args.force or not args.resume)
        plan = reusable or build_plan(workspace, args, storyboard)
        finalize_outputs(workspace, plan, state, result, reused=reusable is not None)
        warnings = scan_for_sensitive_output(result)
        warnings.extend(scan_for_sensitive_output(plan))
        result.setdefault("warnings", []).extend(warnings)
        if warnings:
            result["status"] = "failed"
            result.setdefault("blocked_reasons", []).append({
                "code": "sensitive_output_detected",
                "message": "Sensitive-looking content detected in tool output.",
            })
    except BlockedError as exc:
        ensure_tool_dirs(workspace) if workspace.exists() and workspace.is_dir() else None
        add_block(result, exc.code, exc.message)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        ensure_tool_dirs(workspace) if workspace.exists() and workspace.is_dir() else None
        result["status"] = "failed"
        result.setdefault("blocked_reasons", []).append({"code": "unexpected_error", "message": str(exc)})
    write_json(workspace / REPORT_RESULT_REL, result) if workspace.exists() and workspace.is_dir() else None
    return result


def parse_args(argv: list[str] | None = None) -> Args:
    parser = argparse.ArgumentParser(description="Build an Analysis_V1 Scene/Shot/Task video generation plan.")
    parser.add_argument("--workspace", default=".", help="Analysis_V1 workspace path.")
    parser.add_argument("--target-type", choices=("scene", "shot", "task"), default="task")
    parser.add_argument("--shot-id", default="")
    parser.add_argument("--scene-id", default="")
    parser.add_argument("--max-video-seconds", type=float, default=4.0)
    parser.add_argument("--min-video-seconds", type=float, default=4.0)
    parser.add_argument("--split-tolerance-seconds", type=float, default=1.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(
        workspace=ns.workspace,
        target_type=ns.target_type,
        shot_id=ns.shot_id,
        scene_id=ns.scene_id,
        max_video_seconds=float(ns.max_video_seconds),
        min_video_seconds=float(ns.min_video_seconds),
        split_tolerance_seconds=float(ns.split_tolerance_seconds),
        force=bool(ns.force),
        resume=bool(ns.resume),
        print_json=bool(ns.print_json),
    )


def main(argv: list[str] | None = None) -> int:
    cli_args = argv if argv is not None else sys.argv[1:]
    if "--tool-session-root" in cli_args:
        try:
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge
        except ModuleNotFoundError:
            repo_root = str(Path(__file__).resolve().parents[2])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge

        framework_exit = maybe_run_framework_bridge(cli_args, script_path=Path(__file__), tool_name=TOOL_NAME)
        if framework_exit is not None:
            return framework_exit

    args = parse_args(cli_args)
    result = run(args)
    if args.print_json:
        print(json.dumps(json_safe(result), ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"failed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
