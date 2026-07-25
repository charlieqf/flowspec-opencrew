from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "05_04_ImagePlanExecutor"
TOOL_VERSION = "0.1.0"
TOOL_DIR_NAME = "S11_05_04_ImagePlanExecutor"
VARIABLES_REL = "SessionContext/Variables.json"
STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
IMAGE_PLAN_REL = "SessionOutput/storyboard/image_generation_plan.json"
STORYBOARD_WORKING_REL = "SessionOutput/storyboard/Working"
ASSET_HISTORY_REL = "SessionOutput/storyboard/assets/history"
RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
EXECUTION_RESULT_REL = f"{TOOL_DIR_NAME}/Output/image_plan_execution_result.json"
SESSION_EXECUTION_RESULT_REL = "SessionOutput/storyboard/image_plan_execution_result.json"
EXECUTION_STATE_REL = f"{TOOL_DIR_NAME}/Output/image_plan_execution_state.json"
SESSION_EXECUTION_STATE_REL = "SessionOutput/storyboard/image_plan_execution_state.json"
SECRET_PATTERNS = ("postgresql://", "postgresql+psycopg://", "password", "api_key", "apikey", "access_token", "authorization", "bearer ", "cookie")
SAFE_SENSITIVE_METADATA_KEYS = {
    "api_key_ref",
    "has_api_key",
    "secret_length",
}


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Args:
    workspace: str
    database_url: str
    mode: str
    image_provider: str
    image_model: str
    source_plan_hash: str
    target_task_id: str
    target_asset_key: str
    only_missing: bool
    allow_stale_prompt: bool
    overwrite_prompt: bool
    overwrite_image: bool
    force: bool
    resume: bool
    provider_timeout_seconds: int
    print_json: bool


def load_video_plan_executor() -> Any:
    path = Path(__file__).with_name("05_02_VideoPlanExecutor.py")
    spec = importlib.util.spec_from_file_location("analysis_v1_05_02_video_plan_executor_for_image_plan", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VPE = load_video_plan_executor()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_ms() -> int:
    return int(time.time() * 1000)


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def json_safe(value: Any) -> Any:
    return VPE.json_safe(value)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def read_json_or_empty(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except Exception:
        return {}


def workspace_path(workspace: Path, rel_path: str) -> Path:
    path = Path(rel_path)
    return path if path.is_absolute() else workspace / path


def rel(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def resolve_workspace(raw_workspace: str) -> Path:
    return Path(raw_workspace or ".").expanduser().resolve()


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def ensure_tool_dirs(workspace: Path) -> None:
    for rel_path in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", f"{TOOL_DIR_NAME}/Prompt", f"{TOOL_DIR_NAME}/Report", STORYBOARD_WORKING_REL, ASSET_HISTORY_REL):
        (workspace / rel_path).mkdir(parents=True, exist_ok=True)


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    tool_dir = workspace / TOOL_DIR_NAME
    if tool_dir.exists():
        remove_path(tool_dir)
        result.setdefault("cleanup_actions", []).append({"path": TOOL_DIR_NAME, "action": "removed_for_force_rerun"})


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


def load_required_json(workspace: Path, rel_path: str, code: str) -> dict[str, Any]:
    path = workspace / rel_path
    if not path.exists():
        raise ToolError(f"{code}: missing required file {rel_path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ToolError(f"{code}: {rel_path} must contain a JSON object")
    return payload


def configure_vpe_hooks() -> None:
    VPE.TOOL_DIR_NAME = TOOL_DIR_NAME
    VPE.RESULT_REL = RESULT_REL
    VPE.backup_before_overwrite = backup_before_overwrite
    VPE.backup_before_overwrite_once = backup_before_overwrite_once


def slot_for_path(rel_path: str) -> str:
    name = Path(rel_path).name
    if "_ImagePrompt" in name:
        return "ImagePrompt"
    if "_Image_" in name:
        return "Image_New"
    return Path(rel_path).stem


def history_item_for(original_rel: str, history_rel: str, reason: str) -> dict[str, Any]:
    return {
        "original_path": original_rel,
        "history_path": history_rel,
        "slot": slot_for_path(original_rel),
        "asset_key": Path(original_rel).name.split("_Image", 1)[0],
        "reason": reason,
        "source": "05_04",
    }


def backup_before_overwrite(workspace: Path, target: Path, result: dict[str, Any]) -> None:
    if not target.exists():
        return
    batch = result.setdefault("_runtime_flags", {}).setdefault("backup_batch_id", f"batch_{now_ms()}_05_04_image_plan_executor_backup")
    history = workspace / ASSET_HISTORY_REL / batch
    history.mkdir(parents=True, exist_ok=True)
    backup = history / target.name
    counter = 1
    while backup.exists():
        backup = history / f"{backup.stem}_{counter}{backup.suffix}"
        counter += 1
    shutil.copy2(target, backup)
    original_rel = rel(workspace, target)
    history_rel = rel(workspace, backup)
    manifest_path = history / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {"schema_version": "storyboard_asset_history_0.1", "batch_id": batch, "reason": "05_04_image_plan_executor_backup", "created_at": now_ms(), "items": []}
    items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    items.append(history_item_for(original_rel, history_rel, "05_04_image_plan_executor_backup"))
    manifest["items"] = items
    manifest["updated_at"] = now_ms()
    write_json(manifest_path, manifest)
    result.setdefault("backups", []).append({"from": original_rel, "to": history_rel, "history_path": history_rel})


def backup_before_overwrite_once(workspace: Path, target: Path, result: dict[str, Any], flag: str) -> None:
    flags = result.setdefault("_runtime_flags", {})
    if flags.get(flag):
        return
    backup_before_overwrite(workspace, target, result)
    flags[flag] = True


def append_created_file(result: dict[str, Any], rel_path: str) -> None:
    created = result.setdefault("created_files", [])
    if rel_path not in created:
        created.append(rel_path)


def publish_file(workspace: Path, source: Path, planned_rel: str, result: dict[str, Any]) -> str:
    if not source.exists() or source.stat().st_size <= 0:
        raise ToolError(f"Cannot publish missing or empty file: {source}")
    output_target = workspace / TOOL_DIR_NAME / "Output" / Path(planned_rel).name
    output_target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != output_target.resolve():
        shutil.copy2(source, output_target)
    story_target = workspace_path(workspace, planned_rel)
    story_target.parent.mkdir(parents=True, exist_ok=True)
    backup_before_overwrite(workspace, story_target, result)
    shutil.copy2(output_target, story_target)
    append_created_file(result, rel(workspace, output_target))
    append_created_file(result, rel(workspace, story_target))
    return rel(workspace, story_target)


def copy_templates_to_prompt(prompt_dir: Path, result: dict[str, Any], workspace: Path) -> None:
    for name, rel_path in VPE.MODULE_REFERENCE_TEMPLATE_RELS.items():
        if not name.startswith("Image_"):
            continue
        source = Path(__file__).resolve().parents[2] / Path(rel_path).relative_to("OpenCrew")
        if not source.exists():
            source = workspace / rel_path
        if source.exists():
            target = prompt_dir / f"Ref_05_02_{name}.md"
            shutil.copy2(source, target)
            append_created_file(result, rel(workspace, target))


def copy_inputs_to_working(workspace: Path, variables: dict[str, Any], storyboard: dict[str, Any], image_plan: dict[str, Any], result: dict[str, Any]) -> None:
    working = workspace / TOOL_DIR_NAME / "Working"
    prompt = workspace / TOOL_DIR_NAME / "Prompt"
    write_json(working / "InputFrom_0_Variables.json", variables)
    write_json(working / "InputFrom_7_srt_storyboard.json", storyboard)
    write_json(working / "InputFrom_10_image_generation_plan.json", image_plan)
    copy_templates_to_prompt(prompt, result, workspace)
    for path in ("InputFrom_0_Variables.json", "InputFrom_7_srt_storyboard.json", "InputFrom_10_image_generation_plan.json"):
        append_created_file(result, rel(workspace, working / path))


def select_tasks(image_plan: dict[str, Any], args: Args) -> list[dict[str, Any]]:
    tasks = [item for item in list_value(image_plan.get("image_tasks")) if isinstance(item, dict)]
    if args.target_task_id:
        tasks = [item for item in tasks if text_value(item.get("image_task_id")) == args.target_task_id]
    if args.target_asset_key:
        tasks = [item for item in tasks if text_value(item.get("asset_key")) == args.target_asset_key]
    return tasks


def prompt_path_for(task: dict[str, Any]) -> str:
    return text_value(dict_value(task.get("planned_outputs")).get("image_prompt_path")) or f"{STORYBOARD_WORKING_REL}/{text_value(task.get('asset_key'))}_ImagePrompt.json"


def image_path_for(task: dict[str, Any]) -> str:
    return text_value(dict_value(task.get("planned_outputs")).get("image_path")) or f"{STORYBOARD_WORKING_REL}/{text_value(task.get('asset_key'))}_Image_New.png"


def image_task_executable(task: dict[str, Any]) -> bool:
    status = text_value(task.get("status"))
    planned = dict_value(task.get("planned_outputs"))
    return bool(
        text_value(task.get("asset_key"))
        and text_value(planned.get("image_path"))
        and status != "skipped"
        and not status.startswith("blocked")
    )


def prompt_step_available(task: dict[str, Any]) -> bool:
    return bool(image_task_executable(task) and prompt_path_for(task))


def image_step_available(task: dict[str, Any]) -> bool:
    return image_task_executable(task)


def step_state_key(task: dict[str, Any]) -> str:
    return text_value(task.get("asset_key")) or text_value(task.get("image_task_id")) or "unknown"


def initial_execution_marker(args: Args, selected: list[dict[str, Any]]) -> dict[str, Any]:
    task = next((item for item in selected if image_task_executable(item)), selected[0] if selected else {})
    step = ""
    if task:
        step = "image" if args.mode == "image-only" else "prompt"
    task_key = step_state_key(task) if task else ""
    updated_at = now_iso()
    return {
        "current_task_id": text_value(task.get("image_task_id")),
        "current_asset_key": text_value(task.get("asset_key")),
        "current_step": step,
        "current_step_status": "running_generate" if step else "",
        "tasks": {
            task_key: {
                "image_task_id": text_value(task.get("image_task_id")),
                "asset_key": text_value(task.get("asset_key")),
                "steps": {
                    step: {
                        "status": "running_generate",
                        "updated_at": updated_at,
                    }
                },
            }
        } if task_key and step else {},
    }


def execution_state_base(args: Args, image_plan: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "analysis_v1_image_plan_execution_state_0.1",
        "job_id": f"ip_exec_{now_ms()}",
        "status": "queued",
        "mode": args.mode,
        "source_plan_hash": text_value(image_plan.get("plan_hash")),
        "summary": dict_value(result.get("summary")) if isinstance(result, dict) else {},
        "error": "",
        "started_at": now_iso(),
        "updated_at": now_iso(),
    }


def write_execution_state(workspace: Path, state: dict[str, Any]) -> None:
    write_json(workspace / EXECUTION_STATE_REL, state)
    write_json(workspace / SESSION_EXECUTION_STATE_REL, state)


def update_step_execution_state(
    workspace: Path,
    args: Args,
    image_plan: dict[str, Any],
    task: dict[str, Any],
    step_name: str,
    step_status: str,
    result: dict[str, Any],
    output_path: str = "",
) -> None:
    current = read_json_or_empty(workspace / SESSION_EXECUTION_STATE_REL)
    state = {
        **execution_state_base(args, image_plan, result),
        **(current if isinstance(current, dict) else {}),
        "status": "running",
        "mode": args.mode,
        "source_plan_hash": text_value(image_plan.get("plan_hash")),
        "current_task_id": text_value(task.get("image_task_id")),
        "current_asset_key": text_value(task.get("asset_key")),
        "current_step": step_name if step_status.startswith("running") else text_value((current if isinstance(current, dict) else {}).get("current_step")),
        "updated_at": now_iso(),
    }
    tasks = state.get("tasks") if isinstance(state.get("tasks"), dict) else {}
    key = step_state_key(task)
    task_state = tasks.get(key) if isinstance(tasks.get(key), dict) else {}
    steps = task_state.get("steps") if isinstance(task_state.get("steps"), dict) else {}
    step_payload = {"status": step_status, "updated_at": now_iso()}
    if output_path:
        step_payload["output_path"] = output_path
    steps[step_name] = {**(steps.get(step_name) if isinstance(steps.get(step_name), dict) else {}), **step_payload}
    tasks[key] = {
        **task_state,
        "image_task_id": text_value(task.get("image_task_id")),
        "asset_key": text_value(task.get("asset_key")),
        "steps": steps,
    }
    state["tasks"] = tasks
    state["current_step_status"] = step_status
    write_execution_state(workspace, state)


def source_video_plan_for(image_plan: dict[str, Any]) -> dict[str, Any]:
    return {"consistency_references": dict_value(dict_value(image_plan.get("source_video_plan")).get("consistency_references"))}


def write_prompt_variables(prompt_dir: Path, asset_key: str, context: dict[str, Any]) -> Path:
    path = prompt_dir / f"PromptVariables_{asset_key}_Image.json"
    payload = {
        "schema_version": "analysis_v1_05_04_image_prompt_variables_0.1",
        "asset_key": asset_key,
        "segment_id": text_value(dict_value(context.get("segment")).get("segment_id")),
        "dialogue_asset_keys": list_value(dict_value(context.get("segment")).get("dialogue_asset_keys") or dict_value(context.get("segment")).get("dialogue_ids")),
        "dialogue_ids": list_value(dict_value(context.get("segment")).get("dialogue_asset_keys") or dict_value(context.get("segment")).get("dialogue_ids")),
        "reference_images": list_value(context.get("references")),
    }
    write_json(path, payload)
    return path


def build_prompt(workspace: Path, variables: dict[str, Any], storyboard: dict[str, Any], image_plan: dict[str, Any], task: dict[str, Any], args: Args, result: dict[str, Any]) -> Path:
    asset_key = text_value(task.get("asset_key"))
    prompt_dir = workspace / TOOL_DIR_NAME / "Prompt"
    segment = dict_value(task.get("source_segment"))
    shot = dict_value(task.get("source_shot"))
    scene = dict_value(task.get("source_scene"))
    if not segment:
        raise ToolError(f"Image task is missing source_segment: {asset_key}")
    references = VPE.prepare_image_references(workspace, segment, source_video_plan_for(image_plan))
    image_selection = VPE.provider_selection(variables, "image", args.image_provider, args.image_model)
    image_module = VPE.image_module_for(image_selection.get("provider", ""), image_selection.get("model", ""))
    context = {
        "workspace": str(workspace),
        "prompt_dir": str(prompt_dir),
        "segment": segment,
        "shot": shot,
        "scene": scene,
        "dialogue_index": VPE.flatten_dialogues(storyboard),
        "references": references,
        "reference_manifests": {},
    }
    variables_path = write_prompt_variables(prompt_dir, asset_key, context)
    package = image_module.build_prompt_package(context)
    package.update({
        "asset_key": asset_key,
        "image_task_id": text_value(task.get("image_task_id")),
        "prompt_status": "draft_generated",
        "prompt_origin": "system_generated",
        "prompt_revision": 1,
        "updated_at": now_iso(),
    })
    rendered_path = image_module.write_prompt_package(prompt_dir, asset_key, package)
    append_created_file(result, rel(workspace, variables_path))
    append_created_file(result, rel(workspace, rendered_path))
    business_prompt = prompt_path_for(task)
    if workspace_path(workspace, business_prompt).exists() and not args.overwrite_prompt:
        return workspace_path(workspace, business_prompt)
    publish_file(workspace, rendered_path, business_prompt, result)
    return workspace_path(workspace, business_prompt)


def prompt_snapshot_for_image(workspace: Path, task: dict[str, Any], result: dict[str, Any]) -> Path:
    asset_key = text_value(task.get("asset_key"))
    business_prompt = workspace_path(workspace, prompt_path_for(task))
    if not business_prompt.exists():
        raise ToolError(f"Image prompt is missing for image-only execution: {prompt_path_for(task)}")
    snapshot = workspace / TOOL_DIR_NAME / "Prompt" / f"PromptRendered_{asset_key}_ImagePrompt.json"
    if business_prompt.resolve() != snapshot.resolve():
        shutil.copy2(business_prompt, snapshot)
    append_created_file(result, rel(workspace, snapshot))
    return snapshot


def prompt_file_status(workspace: Path, task: dict[str, Any]) -> tuple[bool, str]:
    business_prompt = workspace_path(workspace, prompt_path_for(task))
    if not business_prompt.exists():
        return False, f"Image prompt is missing for image-only execution: {prompt_path_for(task)}"
    prompt_payload = read_json(business_prompt)
    if not isinstance(prompt_payload, dict):
        return False, f"Image prompt must be a JSON object: {prompt_path_for(task)}"
    return True, ""


def execute_image(workspace: Path, variables: dict[str, Any], image_plan: dict[str, Any], storyboard: dict[str, Any], task: dict[str, Any], args: Args, result: dict[str, Any]) -> str:
    asset_key = text_value(task.get("asset_key"))
    image_rel = image_path_for(task)
    if args.only_missing and workspace_path(workspace, image_rel).exists():
        return image_rel
    if workspace_path(workspace, image_rel).exists() and not args.overwrite_image:
        return image_rel
    prompt_exists, prompt_reason = prompt_file_status(workspace, task)
    if not prompt_exists:
        raise ToolError(prompt_reason)
    segment = dict_value(task.get("source_segment"))
    prompt_snapshot = prompt_snapshot_for_image(workspace, task, result)
    references = VPE.prepare_image_references(workspace, segment, source_video_plan_for(image_plan))
    image_reference_paths = [workspace_path(workspace, item["working_path"]) for item in references if text_value(item.get("working_path"))]
    target = VPE.reference_by_kind(references, "target_frame")
    target_frame_path = workspace_path(workspace, text_value(target.get("working_path"))) if text_value(target.get("working_path")) else None
    image_config = VPE.load_provider_config(args, variables, "image", args.image_provider, args.image_model)
    working_output = workspace / TOOL_DIR_NAME / "Working" / f"{asset_key}_Image_New.png"
    request = {
        "provider_config": VPE.redact_config(image_config),
        "prompt_path": rel(workspace, prompt_snapshot),
        "reference_count": len(image_reference_paths),
        "reference_paths": [rel(workspace, path) for path in image_reference_paths],
        "reference_roles": references,
        "target_aspect": "9:16",
    }
    VPE.record_model_call(workspace / TOOL_DIR_NAME / "Prompt", asset_key, "Image", request)
    response = VPE.generate_image_with_provider(image_config, prompt_snapshot, working_output, image_reference_paths, args.provider_timeout_seconds)
    response["dimension_normalization"] = VPE.normalize_image_to_target_aspect(working_output, target_frame_path)
    VPE.record_model_call(workspace / TOOL_DIR_NAME / "Prompt", asset_key, "Image", request, response)
    published = publish_file(workspace, working_output, image_rel, result)
    dialogue_index = VPE.flatten_dialogues(storyboard)
    if VPE.bind_segment_output_to_storyboard(segment, dialogue_index, "image", published):
        VPE.persist_storyboard_asset_bindings(workspace, storyboard, result)
    else:
        raise ToolError(f"Generated image could not be bound to storyboard dialogue: {asset_key}")
    return published


def execute_task(workspace: Path, variables: dict[str, Any], storyboard: dict[str, Any], image_plan: dict[str, Any], task: dict[str, Any], args: Args, result: dict[str, Any]) -> dict[str, Any]:
    asset_key = text_value(task.get("asset_key"))
    steps = dict_value(task.get("steps"))
    prompt_required = bool(dict_value(steps.get("prompt")).get("required"))
    image_required = bool(dict_value(steps.get("image")).get("required"))
    task_status = text_value(task.get("status"))
    blocked_reason = text_value(task.get("blocked_reason")) or text_value(task.get("error"))
    task_result: dict[str, Any] = {
        "image_task_id": text_value(task.get("image_task_id")),
        "asset_key": asset_key,
        "status": "completed",
        "steps": {},
        "outputs": {},
        "error": "",
    }
    if task_status.startswith("blocked"):
        task_result["status"] = "blocked"
        task_result["error"] = blocked_reason or task_status
        return task_result
    prompt_available = prompt_required or prompt_step_available(task)
    image_available = image_required or image_step_available(task)
    if args.mode == "prompt-only" and not prompt_available:
        task_result["status"] = "skipped"
        return task_result
    if args.mode == "image-only" and not image_available:
        task_result["status"] = "skipped"
        return task_result
    if args.mode == "prompt-and-image" and not (prompt_available or image_available):
        task_result["status"] = "skipped"
        return task_result
    active_step = "prompt"
    try:
        if args.mode in {"prompt-only", "prompt-and-image"} and prompt_available:
            active_step = "prompt"
            update_step_execution_state(workspace, args, image_plan, task, "prompt", "running_generate", result)
            prompt_path = build_prompt(workspace, variables, storyboard, image_plan, task, args, result)
            task_result["steps"]["prompt"] = {"status": "completed_working", "output_path": rel(workspace, prompt_path)}
            task_result["outputs"]["image_prompt_path"] = rel(workspace, prompt_path)
            update_step_execution_state(workspace, args, image_plan, task, "prompt", "completed_working", result, rel(workspace, prompt_path))
        if args.mode in {"image-only", "prompt-and-image"} and image_available:
            active_step = "image"
            update_step_execution_state(workspace, args, image_plan, task, "image", "running_generate", result)
            image_path = execute_image(workspace, variables, image_plan, storyboard, task, args, result)
            task_result["steps"]["image"] = {"status": "completed_working", "output_path": image_path}
            task_result["outputs"]["image_path"] = image_path
            update_step_execution_state(workspace, args, image_plan, task, "image", "completed_working", result, image_path)
    except Exception as exc:
        task_result["status"] = "failed"
        task_result["error"] = str(exc)
        update_step_execution_state(workspace, args, image_plan, task, active_step, "failed", result)
    return task_result


def summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "task_count": len(results),
        "completed_count": sum(1 for item in results if item.get("status") == "completed"),
        "skipped_count": sum(1 for item in results if item.get("status") == "skipped"),
        "blocked_count": sum(1 for item in results if item.get("status") == "blocked"),
        "failed_count": sum(1 for item in results if item.get("status") == "failed"),
        "prompt_completed_count": sum(1 for item in results if dict_value(item.get("steps")).get("prompt", {}).get("status") == "completed_working"),
        "image_completed_count": sum(1 for item in results if dict_value(item.get("steps")).get("image", {}).get("status") == "completed_working"),
    }


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "mode": args.mode,
        "requires_database": args.mode in {"image-only", "prompt-and-image"},
        "requires_model_calls": args.mode in {"image-only", "prompt-and-image"},
        "created_files": [],
        "cleanup_actions": [],
        "backups": [],
        "tasks": [],
        "summary": {},
        "warnings": [],
        "blocked_reasons": [],
        "updated_at": now_iso(),
    }


def scan_for_sensitive_output(payload: Any) -> list[dict[str, str]]:
    def strip_safe_metadata(value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key).lower()
                if key_text in SAFE_SENSITIVE_METADATA_KEYS or key_text.endswith("_key_ref"):
                    continue
                result[str(key)] = strip_safe_metadata(item)
            return result
        if isinstance(value, list):
            return [strip_safe_metadata(item) for item in value]
        return value

    text = json.dumps(json_safe(strip_safe_metadata(payload)), ensure_ascii=False).lower()
    return [{"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"} for pattern in SECRET_PATTERNS if pattern in text]


def run(args: Args) -> dict[str, Any]:
    configure_vpe_hooks()
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    try:
        if not workspace.exists() or not workspace.is_dir():
            raise ToolError(f"workspace_missing: {workspace}")
        if args.mode not in {"prompt-only", "image-only", "prompt-and-image"}:
            raise ToolError("--mode must be prompt-only, image-only, or prompt-and-image")
        if args.force:
            force_reset(workspace, result)
        ensure_tool_dirs(workspace)
        variables = load_required_json(workspace, VARIABLES_REL, "variables_missing")
        storyboard = load_required_json(workspace, STORYBOARD_REL, "storyboard_missing")
        image_plan = load_required_json(workspace, IMAGE_PLAN_REL, "image_plan_missing")
        source_plan_hash = text_value(args.source_plan_hash or image_plan.get("plan_hash") or VPE.plan_hash(image_plan))
        image_plan = {**image_plan, "plan_hash": source_plan_hash}
        result["source_plan_hash"] = source_plan_hash
        copy_inputs_to_working(workspace, variables, storyboard, image_plan, result)
        selected = select_tasks(image_plan, args)
        if not selected:
            raise ToolError("image_plan_has_no_selected_tasks")
        previous_state = read_json_or_empty(workspace / SESSION_EXECUTION_STATE_REL)
        job_id = text_value(previous_state.get("job_id")) if isinstance(previous_state, dict) else ""
        initial_marker = initial_execution_marker(args, selected)
        initial_state = execution_state_base(args, image_plan, result)
        initial_state.update({
            **(previous_state if isinstance(previous_state, dict) else {}),
            "job_id": job_id or f"ip_exec_{now_ms()}_{uuid.uuid4().hex[:8]}",
            "status": "queued",
            "mode": args.mode,
            "source_plan_hash": text_value(image_plan.get("plan_hash")),
            **initial_marker,
        })
        write_execution_state(workspace, initial_state)
        task_results = []
        for task in selected:
            task_results.append(execute_task(workspace, variables, storyboard, image_plan, task, args, result))
        result["tasks"] = task_results
        result["summary"] = summarize(task_results)
        if result["summary"]["failed_count"]:
            result["status"] = "completed_with_failed_items" if result["summary"]["completed_count"] else "failed"
        elif result["summary"]["blocked_count"]:
            result["status"] = "completed_with_blocked_items"
        previous_state = read_json_or_empty(workspace / SESSION_EXECUTION_STATE_REL)
        write_json(workspace / EXECUTION_RESULT_REL, result)
        write_json(workspace / SESSION_EXECUTION_RESULT_REL, result)
        state = {
            **(previous_state if isinstance(previous_state, dict) else {}),
            "schema_version": "analysis_v1_image_plan_execution_state_0.1",
            "job_id": text_value(previous_state.get("job_id")) if isinstance(previous_state, dict) else f"ip_exec_{now_ms()}_{uuid.uuid4().hex[:8]}",
            "status": result["status"],
            "mode": args.mode,
            "current_task_id": "",
            "current_asset_key": "",
            "current_step": "",
            "current_step_status": "",
            "source_plan_hash": text_value(image_plan.get("plan_hash")),
            "summary": result["summary"],
            "updated_at": now_iso(),
        }
        write_execution_state(workspace, state)
        for rel_path in (EXECUTION_RESULT_REL, SESSION_EXECUTION_RESULT_REL, EXECUTION_STATE_REL, SESSION_EXECUTION_STATE_REL, RESULT_REL):
            append_created_file(result, rel_path)
        warnings = scan_for_sensitive_output(result)
        result.setdefault("warnings", []).extend(warnings)
        if warnings:
            result["status"] = "failed"
            result.setdefault("blocked_reasons", []).append({"code": "sensitive_output_detected", "message": "Sensitive-looking content detected in output files."})
    except Exception as exc:
        ensure_tool_dirs(workspace) if workspace.exists() and workspace.is_dir() else None
        result["status"] = "blocked" if isinstance(exc, ToolError) else "failed"
        result.setdefault("blocked_reasons", []).append({"code": "execution_blocked", "message": str(exc)})
    result["updated_at"] = now_iso()
    result.pop("_runtime_flags", None)
    if workspace.exists() and workspace.is_dir():
        write_json(workspace / RESULT_REL, result)
    return result


def parse_args(argv: list[str] | None = None) -> Args:
    parser = argparse.ArgumentParser(description="Execute Analysis_V1 image plan prompt/image steps.")
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--database-url", default="")
    parser.add_argument("--mode", choices=("prompt-only", "image-only", "prompt-and-image"), default="prompt-only")
    parser.add_argument("--image-provider", default="")
    parser.add_argument("--image-model", default="")
    parser.add_argument("--source-plan-hash", default="")
    parser.add_argument("--target-task-id", default="")
    parser.add_argument("--target-asset-key", default="")
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--allow-stale-prompt", action="store_true")
    parser.add_argument("--overwrite-prompt", action="store_true")
    parser.add_argument("--overwrite-image", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--provider-timeout-seconds", type=int, default=1800)
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(**vars(ns))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    result = run(args)
    if args.print_json:
        print(json.dumps(json_safe(result), ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"completed", "completed_with_failed_items"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
