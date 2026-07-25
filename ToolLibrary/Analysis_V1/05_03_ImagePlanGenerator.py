from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "05_03_ImagePlanGenerator"
TOOL_VERSION = "0.1.0"
TOOL_DIR_NAME = "S10_05_03_ImagePlanGenerator"
VARIABLES_REL = "SessionContext/Variables.json"
STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
SESSION_IMAGE_PLAN_REL = "SessionOutput/storyboard/image_generation_plan.json"
OUTPUT_IMAGE_PLAN_REL = f"{TOOL_DIR_NAME}/Output/image_generation_plan.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_STORYBOARD_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_7_srt_storyboard.json"
WORKING_PARAMS_REL = f"{TOOL_DIR_NAME}/Working/InputParams_image_generation_plan.json"
SECRET_PATTERNS = ("postgresql://", "postgresql+psycopg://", "password", "api_key", "apikey", "access_token", "authorization", "bearer ", "cookie")


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
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
    include_existing_prompts: bool
    include_ready_images: bool
    force: bool
    resume: bool
    print_json: bool


def load_video_plan_generator() -> Any:
    path = Path(__file__).with_name("05_01_VideoPlanGenerator.py")
    spec = importlib.util.spec_from_file_location("analysis_v1_05_01_video_plan_generator_for_image_plan", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VPG = load_video_plan_generator()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(json_safe(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan_hash(plan: dict[str, Any]) -> str:
    payload = {key: value for key, value in plan.items() if key not in {"plan_hash", "created_at"}}
    return stable_hash(payload)


def workspace_path(workspace: Path, rel_path: str) -> Path:
    path = Path(rel_path)
    return path if path.is_absolute() else workspace / path


def file_exists(workspace: Path, rel_path: str) -> bool:
    path = workspace_path(workspace, rel_path)
    return bool(rel_path) and path.exists() and path.is_file() and path.stat().st_size > 0


def resolve_workspace(raw_workspace: str) -> Path:
    return Path(raw_workspace or ".").expanduser().resolve()


def ensure_tool_dirs(workspace: Path) -> None:
    for rel in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", f"{TOOL_DIR_NAME}/Report", "SessionOutput/storyboard"):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    tool_dir = workspace / TOOL_DIR_NAME
    if tool_dir.exists():
        remove_path(tool_dir)
        result.setdefault("cleanup_actions", []).append({"path": TOOL_DIR_NAME, "action": "removed_for_force_rerun"})
    session_plan = workspace / SESSION_IMAGE_PLAN_REL
    if session_plan.exists():
        remove_path(session_plan)
        result.setdefault("cleanup_actions", []).append({"path": SESSION_IMAGE_PLAN_REL, "action": "removed_for_force_rerun"})


def load_required_json(workspace: Path, rel_path: str, code: str) -> dict[str, Any]:
    path = workspace / rel_path
    if not path.exists():
        raise BlockedError(code, f"Missing required file: {rel_path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError(f"{code}_invalid", f"{rel_path} must contain a JSON object.")
    return payload


def validate_args(args: Args) -> None:
    if args.target_type not in {"scene", "shot", "task"}:
        raise BlockedError("target_type_invalid", "--target-type must be scene, shot, or task.")
    if args.target_type == "scene" and (not args.shot_id or not args.scene_id):
        raise BlockedError("scene_target_requires_ids", "--target-type scene requires --shot-id and --scene-id.")
    if args.target_type == "shot" and not args.shot_id:
        raise BlockedError("shot_target_requires_shot_id", "--target-type shot requires --shot-id.")


def vpg_args(args: Args) -> Any:
    return VPG.Args(
        workspace=args.workspace,
        target_type=args.target_type,
        shot_id=args.shot_id,
        scene_id=args.scene_id,
        max_video_seconds=args.max_video_seconds,
        min_video_seconds=args.min_video_seconds,
        split_tolerance_seconds=args.split_tolerance_seconds,
        force=False,
        resume=False,
        print_json=False,
    )


def segment_dialogue_asset_keys(segment: dict[str, Any]) -> list[str]:
    values = list_value(segment.get("dialogue_asset_keys"))
    if not values:
        values = list_value(segment.get("dialogue_ids"))
    return [text_value(item) for item in values if text_value(item)]


def first_dialogue_asset_key(segment: dict[str, Any]) -> str:
    return next(iter(segment_dialogue_asset_keys(segment)), "")


def image_task_id(shot_id: str, scene_id: str, asset_key: str) -> str:
    raw = f"{shot_id}_{scene_id}_{asset_key}_image"
    return "_".join("".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in raw).split())


def planned_prompt_path(segment: dict[str, Any], asset_key: str) -> str:
    outputs = dict_value(segment.get("planned_outputs"))
    return text_value(outputs.get("image_prompt_path")) or f"SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json"


def planned_image_path(segment: dict[str, Any]) -> str:
    return text_value(dict_value(segment.get("planned_outputs")).get("image_path"))


def prompt_status(workspace: Path, prompt_rel: str, dependency_hashes: dict[str, str]) -> tuple[bool, str]:
    if not file_exists(workspace, prompt_rel):
        return False, "missing"
    try:
        prompt = read_json(workspace_path(workspace, prompt_rel))
    except Exception:
        return True, "invalid"
    if not isinstance(prompt, dict):
        return True, "invalid"
    for key, expected in dependency_hashes.items():
        if expected and text_value(prompt.get(key)) and text_value(prompt.get(key)) != expected:
            return True, "stale"
    return True, text_value(prompt.get("prompt_status")) or "available"


def task_status(source_type: str, need_prompt: bool, need_image: bool, image_exists: bool, prompt_exists: bool, prompt_state: str, source_exists: bool) -> str:
    if image_exists and source_type in {"generated_image", "placed_uploaded_image", "original_image"}:
        return "ready_existing_image"
    if source_type == "existing_image_prompt":
        if image_exists:
            return "ready_existing_image"
        if prompt_state == "stale":
            return "stale_prompt"
        if need_image and prompt_exists:
            return "planned_image_from_existing_prompt"
        if need_image:
            return "blocked_missing_image_prompt"
        return "skipped"
    if source_type == "bound_video":
        return "skipped"
    if need_image and not source_exists:
        return "blocked_missing_source_image"
    if prompt_state == "stale":
        return "stale_prompt"
    if need_image and prompt_exists:
        return "planned_image_from_existing_prompt"
    if need_prompt or need_image:
        return "planned_prompt_and_image"
    if source_type in {"generated_image", "placed_uploaded_image"}:
        return "ready_existing_image"
    return "skipped"


def build_image_tasks(workspace: Path, storyboard: dict[str, Any], video_plan: dict[str, Any], args: Args) -> list[dict[str, Any]]:
    dialogue_index = VPG.flatten_scenes(storyboard)
    del dialogue_index
    actual_dialogues = {}
    for ref in VPG.flatten_scenes(storyboard):
        for index, dialogue in enumerate(VPG.scene_dialogues(ref.scene)):
            key = VPG.dialogue_key(dialogue, index)
            actual_dialogues[key] = {"shot": ref.shot, "scene": ref.scene, "dialogue": dialogue}

    tasks: list[dict[str, Any]] = []
    for shot in list_value(video_plan.get("shots")):
        if not isinstance(shot, dict):
            continue
        shot_id = text_value(shot.get("shot_id"))
        for scene in list_value(shot.get("scenes")):
            if not isinstance(scene, dict):
                continue
            scene_id = text_value(scene.get("scene_id"))
            for segment in list_value(scene.get("segments")):
                if not isinstance(segment, dict):
                    continue
                segment_tasks = dict_value(segment.get("tasks"))
                first_frame = dict_value(segment.get("first_frame"))
                asset_key = text_value(first_dialogue_asset_key(segment) or segment.get("asset_key"))
                if not asset_key:
                    continue
                image_rel = planned_image_path(segment)
                prompt_rel = planned_prompt_path(segment, asset_key)
                source_rel = text_value(first_frame.get("source_path"))
                source_hash = file_hash(workspace_path(workspace, source_rel))
                dependency_hashes = {
                    "source_plan_hash": text_value(video_plan.get("plan_hash")),
                    "source_storyboard_hash": stable_hash(storyboard),
                    "source_image_hash": source_hash,
                }
                has_prompt, prompt_state = prompt_status(workspace, prompt_rel, dependency_hashes)
                has_image = file_exists(workspace, image_rel) if image_rel else False
                source_exists = file_exists(workspace, source_rel)
                need_prompt = bool(segment_tasks.get("need_image_prompt"))
                need_image = bool(segment_tasks.get("need_image"))
                status = task_status(text_value(first_frame.get("source_type")), need_prompt, need_image, has_image, has_prompt, prompt_state, source_exists)
                if status == "ready_existing_image" and not args.include_ready_images:
                    continue
                if has_prompt and not args.include_existing_prompts and status == "planned_image_from_existing_prompt":
                    status = "planned_prompt_and_image"
                if status == "skipped" and not args.include_ready_images:
                    continue
                actual = dict_value(actual_dialogues.get(asset_key))
                task = {
                    "image_task_id": image_task_id(shot_id, scene_id, asset_key),
                    "asset_key": asset_key,
                    "shot_id": shot_id,
                    "scene_id": scene_id,
                    "dialogue_asset_keys": segment_dialogue_asset_keys(segment),
                    "dialogue_ids": segment_dialogue_asset_keys(segment),
                    "dialogue_text": "",
                    "status": status,
                    "source_video_plan_segment_id": text_value(segment.get("segment_id")),
                    "source_video_plan_hash": text_value(video_plan.get("plan_hash")),
                    "source_image": {
                        "source_type": text_value(first_frame.get("source_type")),
                        "source_path": source_rel,
                        "role": "TARGET_FRAME",
                        "exists": source_exists,
                        "sha256": source_hash,
                    },
                    "existing_assets": {
                        "image_path": image_rel,
                        "image_exists": has_image,
                        "prompt_path": prompt_rel,
                        "prompt_exists": has_prompt,
                        "prompt_status": prompt_state,
                    },
                    "planned_outputs": {
                        "image_prompt_path": prompt_rel,
                        "image_path": image_rel,
                    },
                    "steps": {
                        "prompt": {
                            "required": bool(need_prompt and status not in {"ready_existing_image", "skipped"} and not status.startswith("blocked")),
                            "status": "completed_working" if has_prompt and prompt_state != "stale" else ("stale" if prompt_state == "stale" else "pending"),
                            "can_edit_after_generate": True,
                            "executor_mode": "prompt",
                        },
                        "image": {
                            "required": bool(need_image and status not in {"ready_existing_image", "skipped"} and not status.startswith("blocked")),
                            "status": "completed_working" if has_image else ("blocked" if status.startswith("blocked") else "pending"),
                            "depends_on_prompt": bool(need_image),
                            "executor_mode": "image",
                        },
                    },
                    "references": {
                        "host_reference": {"required": False, "path": "", "exists": False},
                        "product_reference": {"required": False, "path": "", "exists": False},
                    },
                    "blocked_reason": "missing_image_prompt" if status == "blocked_missing_image_prompt" else ("missing_image_source" if status == "blocked_missing_source_image" else ""),
                    "source_segment": segment,
                    "source_shot": actual.get("shot") or shot,
                    "source_scene": actual.get("scene") or scene,
                }
                dialogue = dict_value(actual.get("dialogue"))
                task["dialogue_text"] = text_value(dialogue.get("dialogue") or dialogue.get("text"))
                tasks.append(task)
    return tasks


def summarize_tasks(tasks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_tasks": len(tasks),
        "planned_prompt_tasks": sum(1 for item in tasks if dict_value(item.get("steps")).get("prompt", {}).get("required")),
        "planned_image_tasks": sum(1 for item in tasks if dict_value(item.get("steps")).get("image", {}).get("required")),
        "ready_existing_images": sum(1 for item in tasks if item.get("status") == "ready_existing_image"),
        "existing_prompts": sum(1 for item in tasks if dict_value(item.get("existing_assets")).get("prompt_exists")),
        "stale_prompts": sum(1 for item in tasks if item.get("status") == "stale_prompt"),
        "blocked_tasks": sum(1 for item in tasks if text_value(item.get("status")).startswith("blocked")),
    }


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
        "cleanup_actions": [],
        "inputs": {},
        "outputs": {},
        "summary": {},
        "warnings": [],
        "blocked_reasons": [],
        "force": bool(args.force),
        "resume": bool(args.resume),
        "updated_at": now_iso(),
    }


def prepare_inputs(workspace: Path, variables: dict[str, Any], storyboard: dict[str, Any], args: Args, result: dict[str, Any]) -> dict[str, Any]:
    ensure_tool_dirs(workspace)
    params = {
        "target_type": args.target_type,
        "shot_id": args.shot_id,
        "scene_id": args.scene_id,
        "max_video_seconds": args.max_video_seconds,
        "min_video_seconds": args.min_video_seconds,
        "split_tolerance_seconds": args.split_tolerance_seconds,
        "include_existing_prompts": args.include_existing_prompts,
        "include_ready_images": args.include_ready_images,
    }
    write_json(workspace / WORKING_VARIABLES_REL, variables)
    write_json(workspace / WORKING_STORYBOARD_REL, storyboard)
    write_json(workspace / WORKING_PARAMS_REL, params)
    state = {
        "tool": TOOL_NAME,
        "status": "ready",
        "phase": "prepare",
        "input_hashes": {"variables": stable_hash(variables), "storyboard": stable_hash(storyboard), "params": stable_hash(params)},
        "inputs": {"variables": WORKING_VARIABLES_REL, "storyboard": WORKING_STORYBOARD_REL, "params": WORKING_PARAMS_REL},
        "updated_at": now_iso(),
    }
    write_json(workspace / WORKING_STATE_REL, state)
    result["inputs"] = state["inputs"]
    return state


def load_reusable_plan(workspace: Path, state: dict[str, Any], force: bool) -> dict[str, Any] | None:
    if force or not (workspace / OUTPUT_IMAGE_PLAN_REL).exists() or not (workspace / WORKING_STATE_REL).exists():
        return None
    try:
        existing_state = read_json(workspace / WORKING_STATE_REL)
        plan = read_json(workspace / OUTPUT_IMAGE_PLAN_REL)
    except Exception:
        return None
    if not isinstance(plan, dict) or existing_state.get("input_hashes") != state.get("input_hashes"):
        return None
    return plan


def build_plan(workspace: Path, args: Args, storyboard: dict[str, Any]) -> dict[str, Any]:
    video_plan = VPG.build_plan(workspace, vpg_args(args), storyboard)
    video_plan["plan_hash"] = VPG.plan_hash(video_plan)
    tasks = build_image_tasks(workspace, storyboard, video_plan, args)
    plan = {
        "schema_version": "analysis_v1_image_generation_plan_0.1",
        "tool_name": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "plan_run_id": f"ip_{int(time.time())}",
        "source_storyboard_path": STORYBOARD_REL,
        "target": {"target_type": args.target_type, "shot_id": args.shot_id, "scene_id": args.scene_id},
        "source": {
            "storyboard_path": STORYBOARD_REL,
            "storyboard_hash": stable_hash(storyboard),
            "video_generation_plan_hash": text_value(video_plan.get("plan_hash")),
            "video_generation_plan_source": "generated_with_05_01_logic",
        },
        "source_video_plan": {
            "plan_hash": text_value(video_plan.get("plan_hash")),
            "consistency_references": dict_value(video_plan.get("consistency_references")),
        },
        "summary": summarize_tasks(tasks),
        "image_tasks": tasks,
        "created_at": now_iso(),
    }
    plan["plan_hash"] = plan_hash(plan)
    return plan


def scan_for_sensitive_output(payload: Any) -> list[dict[str, str]]:
    text = json.dumps(json_safe(payload), ensure_ascii=False).lower()
    return [{"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"} for pattern in SECRET_PATTERNS if pattern in text]


def finalize_outputs(workspace: Path, plan: dict[str, Any], result: dict[str, Any], reused: bool) -> None:
    write_json(workspace / OUTPUT_IMAGE_PLAN_REL, plan)
    write_json(workspace / SESSION_IMAGE_PLAN_REL, plan)
    result["status"] = "completed_with_blocked_items" if plan["summary"].get("blocked_tasks") else "completed"
    result["outputs"] = {"tool_output": OUTPUT_IMAGE_PLAN_REL, "session_output": SESSION_IMAGE_PLAN_REL}
    result["summary"] = plan.get("summary") or {}
    result["created_files"] = [WORKING_VARIABLES_REL, WORKING_STORYBOARD_REL, WORKING_PARAMS_REL, WORKING_STATE_REL, OUTPUT_IMAGE_PLAN_REL, SESSION_IMAGE_PLAN_REL, REPORT_RESULT_REL]
    if reused:
        result.setdefault("warnings", []).append({"code": "reused_completed_output", "message": "Reused completed image generation plan because input hashes matched."})


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    try:
        if not workspace.exists() or not workspace.is_dir():
            raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
        validate_args(args)
        if args.force:
            force_reset(workspace, result)
        variables = load_required_json(workspace, VARIABLES_REL, "variables_missing")
        storyboard = load_required_json(workspace, STORYBOARD_REL, "storyboard_missing")
        state = prepare_inputs(workspace, variables, storyboard, args, result)
        reusable = load_reusable_plan(workspace, state, force=args.force or not args.resume)
        plan = reusable or build_plan(workspace, args, storyboard)
        finalize_outputs(workspace, plan, result, reused=reusable is not None)
        warnings = scan_for_sensitive_output(result) + scan_for_sensitive_output(plan)
        result.setdefault("warnings", []).extend(warnings)
        if warnings:
            result["status"] = "failed"
            result.setdefault("blocked_reasons", []).append({"code": "sensitive_output_detected", "message": "Sensitive-looking content detected in tool output."})
    except BlockedError as exc:
        ensure_tool_dirs(workspace) if workspace.exists() and workspace.is_dir() else None
        result["status"] = "blocked"
        result.setdefault("blocked_reasons", []).append({"code": exc.code, "message": exc.message})
    except Exception as exc:
        ensure_tool_dirs(workspace) if workspace.exists() and workspace.is_dir() else None
        result["status"] = "failed"
        result.setdefault("blocked_reasons", []).append({"code": "unexpected_error", "message": str(exc)})
    result["updated_at"] = now_iso()
    if workspace.exists() and workspace.is_dir():
        write_json(workspace / REPORT_RESULT_REL, result)
    return result


def parse_args(argv: list[str] | None = None) -> Args:
    parser = argparse.ArgumentParser(description="Build an Analysis_V1 image generation sub-plan from the saved StoryBoard state.")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--target-type", choices=("scene", "shot", "task"), default="task")
    parser.add_argument("--shot-id", default="")
    parser.add_argument("--scene-id", default="")
    parser.add_argument("--max-video-seconds", type=float, default=4.0)
    parser.add_argument("--min-video-seconds", type=float, default=4.0)
    parser.add_argument("--split-tolerance-seconds", type=float, default=1.0)
    parser.add_argument("--include-existing-prompts", action="store_true", default=True)
    parser.add_argument("--no-include-existing-prompts", dest="include_existing_prompts", action="store_false")
    parser.add_argument("--include-ready-images", action="store_true", default=True)
    parser.add_argument("--no-include-ready-images", dest="include_ready_images", action="store_false")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(**vars(ns))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    result = run(args)
    if args.print_json:
        print(json.dumps(json_safe(result), ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"failed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
