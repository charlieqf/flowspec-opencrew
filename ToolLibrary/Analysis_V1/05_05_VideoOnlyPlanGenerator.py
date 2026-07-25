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


TOOL_NAME = "05_05_VideoOnlyPlanGenerator"
TOOL_VERSION = "0.1.0"
TOOL_DIR_NAME = "S12_05_05_VideoOnlyPlanGenerator"
VARIABLES_REL = "SessionContext/Variables.json"
STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
STORYBOARD_WORKING_REL = "SessionOutput/storyboard/Working"
SESSION_PLAN_REL = "SessionOutput/storyboard/video_only_generation_plan.json"
OUTPUT_PLAN_REL = f"{TOOL_DIR_NAME}/Output/video_only_generation_plan.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
WORKING_STATE_REL = f"{TOOL_DIR_NAME}/Working/State_progress.json"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_STORYBOARD_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_7_srt_storyboard.json"
WORKING_PARAMS_REL = f"{TOOL_DIR_NAME}/Working/InputParams_video_only_generation_plan.json"
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
    force: bool
    resume: bool
    print_json: bool


def load_video_plan_generator() -> Any:
    path = Path(__file__).with_name("05_01_VideoPlanGenerator.py")
    spec = importlib.util.spec_from_file_location("analysis_v1_05_01_video_plan_generator_for_video_only", path)
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


def plan_hash(plan: dict[str, Any]) -> str:
    payload = {key: value for key, value in plan.items() if key not in {"plan_hash", "created_at"}}
    return stable_hash(payload)


def file_hash(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    session_plan = workspace / SESSION_PLAN_REL
    if session_plan.exists():
        remove_path(session_plan)
        result.setdefault("cleanup_actions", []).append({"path": SESSION_PLAN_REL, "action": "removed_for_force_rerun"})


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


def safe_task_id(shot_id: str, scene_id: str, asset_key: str) -> str:
    raw = f"{shot_id}_{scene_id}_{asset_key}_video_only"
    return "_".join("".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in raw).split())


def planned_prompt_path(segment: dict[str, Any], asset_key: str) -> str:
    outputs = dict_value(segment.get("planned_outputs"))
    return text_value(outputs.get("video_prompt_path")) or f"{STORYBOARD_WORKING_REL}/{asset_key}_VideoPrompt.json"


def planned_raw_path(asset_key: str) -> str:
    return f"{STORYBOARD_WORKING_REL}/{asset_key}_Video_Raw.mp4"


def planned_final_path(asset_key: str) -> str:
    return f"{STORYBOARD_WORKING_REL}/{asset_key}_Video_Final.mp4"


def planned_tail_path(asset_key: str) -> str:
    return f"{STORYBOARD_WORKING_REL}/{asset_key}_TailFrame.png"


def video_bound_to_storyboard(storyboard: dict[str, Any], final_rel: str) -> bool:
    for ref in VPG.flatten_scenes(storyboard):
        for index, dialogue in enumerate(VPG.scene_dialogues(ref.scene)):
            slot = VPG.video_slot(dialogue, ref.scene)
            if text_value(slot.get("path")) == final_rel:
                return True
    return False


def actual_dialogues_by_asset(storyboard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    actual: dict[str, dict[str, Any]] = {}
    for ref in VPG.flatten_scenes(storyboard):
        for index, dialogue in enumerate(VPG.scene_dialogues(ref.scene)):
            entry = {"shot": ref.shot, "scene": ref.scene, "dialogue": dialogue}
            key = VPG.dialogue_key(dialogue, index)
            if key:
                actual[key] = entry
    return actual


def bound_final_path_for_dialogue(workspace: Path, dialogue: dict[str, Any], scene: dict[str, Any]) -> str:
    slot = VPG.video_slot(dialogue, scene)
    rel_path = text_value(slot.get("path"))
    if rel_path and file_exists(workspace, rel_path):
        return rel_path
    return ""


def task_status(raw_exists: bool, final_exists: bool, final_bound: bool, blocked: bool, prompt_exists: bool) -> str:
    if blocked:
        return "blocked"
    if final_exists and final_bound:
        return "final_completed"
    if raw_exists:
        return "raw_completed_pending_final"
    if prompt_exists:
        return "planned_video_from_existing_prompt"
    return "planned_prompt_and_video"


def segment_audio_ready(workspace: Path, segment_audio_rel: str, audio_tasks: list[Any]) -> bool:
    audio_paths: list[str] = []
    for item in audio_tasks:
        if not isinstance(item, dict):
            continue
        audio_path = text_value(item.get("existing_audio_path")) or text_value(item.get("planned_audio_path"))
        if audio_path:
            audio_paths.append(audio_path)
    if audio_paths:
        return all(file_exists(workspace, audio_path) for audio_path in audio_paths)
    return file_exists(workspace, segment_audio_rel)


def build_video_only_tasks(workspace: Path, storyboard: dict[str, Any], source_plan: dict[str, Any]) -> list[dict[str, Any]]:
    actual_dialogues = actual_dialogues_by_asset(storyboard)
    tasks: list[dict[str, Any]] = []
    for shot in list_value(source_plan.get("shots")):
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
                asset_key = text_value(first_dialogue_asset_key(segment) or segment.get("asset_key"))
                if not asset_key:
                    continue
                segment_tasks = dict_value(segment.get("tasks"))
                first_frame = dict_value(segment.get("first_frame"))
                outputs = dict_value(segment.get("planned_outputs"))
                actual = dict_value(actual_dialogues.get(asset_key))
                dialogue = dict_value(actual.get("dialogue"))
                actual_scene = dict_value(actual.get("scene")) or scene
                prompt_rel = planned_prompt_path(segment, asset_key)
                raw_rel = planned_raw_path(asset_key)
                bound_final_rel = bound_final_path_for_dialogue(workspace, dialogue, actual_scene)
                final_rel = bound_final_rel or planned_final_path(asset_key)
                tail_rel = planned_tail_path(asset_key)
                segment_audio_rel = text_value(outputs.get("segment_audio_path")) or f"{STORYBOARD_WORKING_REL}/{asset_key}_SegmentAudio_Final.wav"
                image_rel = text_value(outputs.get("image_path")) or text_value(first_frame.get("planned_generated_image_path")) or f"{STORYBOARD_WORKING_REL}/{asset_key}_Image_New.png"
                source_rel = text_value(first_frame.get("source_path"))
                prompt_exists = file_exists(workspace, prompt_rel)
                raw_exists = file_exists(workspace, raw_rel)
                final_exists = file_exists(workspace, final_rel)
                final_bound = video_bound_to_storyboard(storyboard, final_rel)
                tail_exists = file_exists(workspace, tail_rel)
                requires_image = bool(segment_tasks.get("need_image") or first_frame.get("requires_generated_image_before_video"))
                first_frame_exists = file_exists(workspace, image_rel)
                first_frame_source_exists = file_exists(workspace, source_rel)
                first_frame_copy_pending = bool(
                    not first_frame_exists
                    and first_frame_source_exists
                    and dict_value(first_frame.get("materialize_first_frame")).get("required")
                )
                blocked = bool(text_value(segment.get("status")) == "blocked" or text_value(segment.get("blocked_reason")))
                status = task_status(raw_exists, final_exists, final_bound, blocked, prompt_exists)
                raw_or_final_exists = raw_exists or final_exists
                final_completed = final_exists and final_bound
                audio_tasks = list_value(segment.get("dialogue_audio_tasks"))
                audio_ready = segment_audio_ready(workspace, segment_audio_rel, audio_tasks)
                task = {
                    "video_only_task_id": safe_task_id(shot_id, scene_id, asset_key),
                    "asset_key": asset_key,
                    "shot_id": shot_id,
                    "scene_id": scene_id,
                    "dialogue_asset_keys": segment_dialogue_asset_keys(segment),
                    "dialogue_ids": segment_dialogue_asset_keys(segment),
                    "dialogue_text": text_value(dialogue.get("dialogue") or dialogue.get("text")),
                    "status": status,
                    "source_video_plan_segment_id": text_value(segment.get("segment_id")),
                    "source_video_plan_hash": text_value(source_plan.get("plan_hash")),
                    "source_segment": segment,
                    "source_shot": actual.get("shot") or shot,
                    "source_scene": actual.get("scene") or scene,
                    "first_frame": {
                        "source_type": text_value(first_frame.get("source_type")),
                        "source_path": source_rel,
                        "planned_image_path": image_rel,
                        "requires_generated_image": requires_image,
                        "exists": first_frame_exists,
                        "copy_pending": first_frame_copy_pending,
                        "source_sha256": file_hash(workspace_path(workspace, source_rel)),
                    },
                    "existing_assets": {
                        "prompt_path": prompt_rel,
                        "prompt_exists": prompt_exists,
                        "raw_path": raw_rel,
                        "raw_exists": raw_exists,
                        "final_path": final_rel,
                        "final_exists": final_exists,
                        "final_bound": final_bound,
                        "tail_frame_path": tail_rel,
                        "tail_frame_exists": tail_exists,
                    },
                    "planned_outputs": {
                        "segment_audio_path": segment_audio_rel,
                        "first_frame_path": image_rel,
                        "video_prompt_path": prompt_rel,
                        "raw_video_path": raw_rel,
                        "final_video_path": final_rel,
                        "tail_frame_path": tail_rel,
                        "video_duration_seconds": outputs.get("video_duration_seconds") or segment.get("planned_video_duration"),
                    },
                    "steps": {
                        "audio": {"required": True, "status": "completed_working" if audio_ready else "pending"},
                        "first_frame": {"required": True, "status": "completed_working" if first_frame_exists else ("pending_copy" if first_frame_copy_pending else "pending")},
                        "prompt": {
                            "required": not raw_or_final_exists,
                            "status": "completed_working" if prompt_exists else ("disabled_consumed_by_video" if raw_or_final_exists else "pending"),
                            "executor_mode": "prompt",
                        },
                        "video": {
                            "required": not raw_or_final_exists,
                            "status": "completed_working" if raw_or_final_exists else "pending",
                            "depends_on_prompt": True,
                            "executor_mode": "video",
                        },
                        "confirm_final": {
                            "required": bool(raw_exists and not final_completed),
                            "status": "completed_working" if final_completed else ("pending" if raw_exists else "disabled"),
                            "manual": True,
                        },
                    },
                    "blocked_reason": text_value(segment.get("blocked_reason")) if blocked else "",
                }
                task.update(VPG.dance_mimic_fields_from_segment(segment))
                tasks.append(task)
    return tasks


def summarize_tasks(tasks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_tasks": len(tasks),
        "planned_prompt_tasks": sum(1 for item in tasks if dict_value(item.get("steps")).get("prompt", {}).get("required")),
        "planned_video_tasks": sum(1 for item in tasks if dict_value(item.get("steps")).get("video", {}).get("required")),
        "raw_completed_count": sum(1 for item in tasks if dict_value(item.get("existing_assets")).get("raw_exists")),
        "final_completed_count": sum(1 for item in tasks if dict_value(item.get("existing_assets")).get("final_exists") and dict_value(item.get("existing_assets")).get("final_bound")),
        "pending_confirm_final_count": sum(1 for item in tasks if dict_value(item.get("steps")).get("confirm_final", {}).get("status") == "pending"),
        "blocked_tasks": sum(1 for item in tasks if text_value(item.get("status")).startswith("blocked")),
        "dance_mimic_reference_video_tasks": sum(1 for item in tasks if text_value(item.get("reference_video_path")) and text_value(item.get("reference_mode")) == VPG.DANCE_MIMIC_REFERENCE_MODE),
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
    if force or not (workspace / OUTPUT_PLAN_REL).exists() or not (workspace / WORKING_STATE_REL).exists():
        return None
    try:
        existing_state = read_json(workspace / WORKING_STATE_REL)
        plan = read_json(workspace / OUTPUT_PLAN_REL)
    except Exception:
        return None
    if not isinstance(plan, dict) or existing_state.get("input_hashes") != state.get("input_hashes"):
        return None
    return plan


def build_plan(workspace: Path, args: Args, storyboard: dict[str, Any]) -> dict[str, Any]:
    source_plan = VPG.build_plan(workspace, vpg_args(args), storyboard)
    source_plan["plan_hash"] = VPG.plan_hash(source_plan)
    tasks = build_video_only_tasks(workspace, storyboard, source_plan)
    plan = {
        "schema_version": "analysis_v1_video_only_generation_plan_0.1",
        "tool_name": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "plan_run_id": f"vop_{int(time.time())}",
        "source_storyboard_path": STORYBOARD_REL,
        "target": {"target_type": args.target_type, "shot_id": args.shot_id, "scene_id": args.scene_id},
        "source": {
            "storyboard_path": STORYBOARD_REL,
            "storyboard_hash": stable_hash(storyboard),
            "video_generation_plan_hash": text_value(source_plan.get("plan_hash")),
            "video_generation_plan_source": "generated_with_05_01_logic",
        },
        "source_video_plan": {
            "plan_hash": text_value(source_plan.get("plan_hash")),
            "consistency_references": dict_value(source_plan.get("consistency_references")),
        },
        "summary": summarize_tasks(tasks),
        "video_only_tasks": tasks,
        "created_at": now_iso(),
    }
    plan["plan_hash"] = plan_hash(plan)
    return plan


def scan_for_sensitive_output(payload: Any) -> list[dict[str, str]]:
    text = json.dumps(json_safe(payload), ensure_ascii=False).lower()
    return [{"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"} for pattern in SECRET_PATTERNS if pattern in text]


def finalize_outputs(workspace: Path, plan: dict[str, Any], result: dict[str, Any], reused: bool) -> None:
    write_json(workspace / OUTPUT_PLAN_REL, plan)
    write_json(workspace / SESSION_PLAN_REL, plan)
    result["status"] = "completed_with_blocked_items" if plan["summary"].get("blocked_tasks") else "completed"
    result["outputs"] = {"tool_output": OUTPUT_PLAN_REL, "session_output": SESSION_PLAN_REL}
    result["summary"] = plan.get("summary") or {}
    result["created_files"] = [WORKING_VARIABLES_REL, WORKING_STORYBOARD_REL, WORKING_PARAMS_REL, WORKING_STATE_REL, OUTPUT_PLAN_REL, SESSION_PLAN_REL, REPORT_RESULT_REL]
    if reused:
        result.setdefault("warnings", []).append({"code": "reused_completed_output", "message": "Reused completed video only generation plan because input hashes matched."})


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
    parser = argparse.ArgumentParser(description="Build an Analysis_V1 video-only generation sub-plan from the saved StoryBoard state.")
    parser.add_argument("--workspace", default=".")
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
    return Args(**vars(ns))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    result = run(args)
    if args.print_json:
        print(json.dumps(json_safe(result), ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"failed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
