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


TOOL_NAME = "05_06_VideoOnlyPlanExecutor"
TOOL_VERSION = "0.1.0"
TOOL_DIR_NAME = "S13_05_06_VideoOnlyPlanExecutor"
VARIABLES_REL = "SessionContext/Variables.json"
STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
PLAN_REL = "SessionOutput/storyboard/video_only_generation_plan.json"
STORYBOARD_WORKING_REL = "SessionOutput/storyboard/Working"
ASSET_HISTORY_REL = "SessionOutput/storyboard/assets/history"
RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
EXECUTION_RESULT_REL = f"{TOOL_DIR_NAME}/Output/video_only_plan_execution_result.json"
SESSION_EXECUTION_RESULT_REL = "SessionOutput/storyboard/video_only_plan_execution_result.json"
EXECUTION_STATE_REL = f"{TOOL_DIR_NAME}/Output/video_only_plan_execution_state.json"
SESSION_EXECUTION_STATE_REL = "SessionOutput/storyboard/video_only_plan_execution_state.json"
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
    video_provider: str
    video_model: str
    tts_provider: str
    tts_model: str
    source_plan_hash: str
    target_task_id: str
    target_asset_key: str
    only_missing: bool
    overwrite_prompt: bool
    overwrite_video: bool
    force: bool
    resume: bool
    provider_timeout_seconds: int
    print_json: bool


def load_video_plan_executor() -> Any:
    path = Path(__file__).with_name("05_02_VideoPlanExecutor.py")
    spec = importlib.util.spec_from_file_location("analysis_v1_05_02_video_plan_executor_for_video_only", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VPE = load_video_plan_executor()
_TALKING_HEAD_PRIVACY_GRID: Any | None = None


def talking_head_privacy_grid_module() -> Any:
    global _TALKING_HEAD_PRIVACY_GRID
    if _TALKING_HEAD_PRIVACY_GRID is not None:
        return _TALKING_HEAD_PRIVACY_GRID
    path = Path(__file__).resolve().parents[1] / "TalkingHead_V1" / "reference_privacy_grid.py"
    spec = importlib.util.spec_from_file_location("talking_head_reference_privacy_grid_for_video_only", path)
    if not spec or not spec.loader:
        raise ToolError(f"Cannot load TalkingHead privacy grid module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _TALKING_HEAD_PRIVACY_GRID = module
    return module


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_ms() -> int:
    return int(time.time() * 1000)


def text_value(value: Any, fallback: str = "") -> str:
    raw = str(value or "").strip()
    return raw or fallback


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


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(json_safe(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def workspace_path(workspace: Path, rel_path: str) -> Path:
    path = Path(rel_path)
    return path if path.is_absolute() else workspace / path


def rel(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def file_exists(workspace: Path, rel_path: str) -> bool:
    path = workspace_path(workspace, rel_path)
    return bool(rel_path) and path.exists() and path.is_file() and path.stat().st_size > 0


def resolve_workspace(raw_workspace: str) -> Path:
    return Path(raw_workspace or ".").expanduser().resolve()


def ensure_tool_dirs(workspace: Path) -> None:
    for rel_path in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", f"{TOOL_DIR_NAME}/Prompt", f"{TOOL_DIR_NAME}/Report", STORYBOARD_WORKING_REL, ASSET_HISTORY_REL):
        (workspace / rel_path).mkdir(parents=True, exist_ok=True)


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


def configure_vpe_hooks() -> None:
    VPE.TOOL_NAME = TOOL_NAME
    VPE.TOOL_DIR_NAME = TOOL_DIR_NAME
    VPE.RESULT_REL = RESULT_REL


def append_created_file(result: dict[str, Any], rel_path: str) -> None:
    created = result.setdefault("created_files", [])
    if rel_path not in created:
        created.append(rel_path)


def slot_for_path(rel_path: str) -> str:
    name = Path(rel_path).name
    if "_VideoPrompt" in name:
        return "VideoPrompt"
    if "_Video_Raw" in name:
        return "Video_Raw"
    if "_Video_Final" in name:
        return "Video_Final"
    if "_TailFrame" in name:
        return "TailFrame"
    return Path(rel_path).stem


def history_item_for(original_rel: str, history_rel: str, reason: str) -> dict[str, Any]:
    stem = Path(original_rel).stem
    asset_key = stem.split("_Video", 1)[0].split("_TailFrame", 1)[0]
    return {
        "original_path": original_rel,
        "history_path": history_rel,
        "asset_type": "Video" if original_rel.endswith(".mp4") else "File",
        "slot": slot_for_path(original_rel),
        "asset_key": asset_key,
        "reason": reason,
        "source": "05_06",
    }


def backup_before_overwrite(workspace: Path, target: Path, result: dict[str, Any]) -> None:
    if not target.exists():
        return
    batch = result.setdefault("_runtime_flags", {}).setdefault("backup_batch_id", f"batch_{now_ms()}_05_06_video_only_backup")
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
    manifest = read_json(manifest_path) if manifest_path.exists() else {"schema_version": "storyboard_asset_history_0.1", "batch_id": batch, "reason": "05_06_video_only_backup", "created_at": now_ms(), "items": []}
    items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    items.append(history_item_for(original_rel, history_rel, "05_06_video_only_backup"))
    manifest["items"] = items
    manifest["updated_at"] = now_ms()
    write_json(manifest_path, manifest)
    result.setdefault("backups", []).append({"from": original_rel, "to": history_rel, "history_path": history_rel})


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


def bind_first_frame_to_storyboard(workspace: Path, storyboard: dict[str, Any], task: dict[str, Any], rel_path: str, source_type: str, result: dict[str, Any]) -> bool:
    rel_path = text_value(rel_path)
    if not rel_path:
        return False
    segment = source_segment(task)
    dialogue_asset_key = next(iter(VPE.segment_dialogue_asset_keys(segment)), "")
    dialogue_index = VPE.flatten_dialogues(storyboard)
    dialogue = dict_value(dict_value(dialogue_index.get(dialogue_asset_key)).get("dialogue"))
    if not dialogue:
        return False
    assets = VPE.ensure_dialogue_assets(dialogue)
    images = assets.get("images") if isinstance(assets.get("images"), list) else []
    if not images:
        images = [{"slot": "Image_New", "source_type": "", "path": ""}, {"slot": "Image_02", "source_type": "", "path": ""}]
        assets["images"] = images
    current = dict_value(images[0])
    next_source_type = text_value(source_type) or text_value(current.get("source_type")) or "generated"
    if text_value(current.get("path")) == rel_path and text_value(current.get("source_type")) == next_source_type and text_value(dialogue.get("bound_image_path")) == rel_path:
        return False
    images[0] = {"slot": "Image_New", "source_type": next_source_type, "path": rel_path}
    dialogue["bound_image_path"] = rel_path
    VPE.persist_storyboard_asset_bindings(workspace, storyboard, result)
    result.setdefault("sync_actions", []).append({
        "code": "video_only_first_frame_bound_to_storyboard",
        "asset_key": text_value(task.get("asset_key")),
        "path": rel_path,
        "source_type": next_source_type,
    })
    return True


def load_required_json(workspace: Path, rel_path: str, code: str) -> dict[str, Any]:
    path = workspace / rel_path
    if not path.exists():
        raise ToolError(f"{code}: missing required file {rel_path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ToolError(f"{code}: {rel_path} must contain a JSON object")
    return payload


def copy_templates_to_prompt(prompt_dir: Path, result: dict[str, Any], workspace: Path) -> None:
    for name, rel_path in VPE.MODULE_REFERENCE_TEMPLATE_RELS.items():
        if not (name.startswith("Video_") or name.startswith("Image_")):
            continue
        source = Path(__file__).resolve().parents[2] / Path(rel_path).relative_to("OpenCrew")
        if not source.exists():
            source = workspace / rel_path
        if source.exists():
            target = prompt_dir / f"Ref_05_02_{name}.md"
            shutil.copy2(source, target)
            append_created_file(result, rel(workspace, target))


def copy_inputs_to_working(workspace: Path, variables: dict[str, Any], storyboard: dict[str, Any], plan: dict[str, Any], result: dict[str, Any], args: Args | None = None) -> None:
    working = workspace / TOOL_DIR_NAME / "Working"
    prompt = workspace / TOOL_DIR_NAME / "Prompt"
    write_json(working / "InputFrom_0_Variables.json", variables)
    write_json(working / "InputFrom_7_srt_storyboard.json", storyboard)
    write_json(working / "InputFrom_12_video_only_generation_plan.json", plan)
    copy_templates_to_prompt(prompt, result, workspace)
    if args is not None and VPE.selected_video_is_wan_rtv(variables, args.video_provider, args.video_model):
        VPE.copy_wan_rtv_reference_video_to_working(workspace, working, result)
    if args is not None and VPE.selected_video_is_openrouter_max_sd_2(variables, args.video_provider, args.video_model):
        VPE.copy_max_sd_2_reference_video_to_working(workspace, working, result)
    if args is not None and VPE.selected_video_is_kling_omni(variables, args.video_provider, args.video_model):
        VPE.copy_kling_omni_reference_video_to_working(workspace, working, result)
    for name in ("InputFrom_0_Variables.json", "InputFrom_7_srt_storyboard.json", "InputFrom_12_video_only_generation_plan.json"):
        append_created_file(result, rel(workspace, working / name))


def select_tasks(plan: dict[str, Any], args: Args) -> list[dict[str, Any]]:
    tasks = [item for item in list_value(plan.get("video_only_tasks")) if isinstance(item, dict)]
    if args.target_task_id:
        tasks = [item for item in tasks if text_value(item.get("video_only_task_id")) == args.target_task_id]
    if args.target_asset_key:
        tasks = [item for item in tasks if text_value(item.get("asset_key")) == args.target_asset_key]
    return tasks


def task_key(task: dict[str, Any]) -> str:
    return text_value(task.get("asset_key")) or text_value(task.get("video_only_task_id")) or "unknown"


def output_rel(task: dict[str, Any], key: str, fallback_suffix: str) -> str:
    planned = dict_value(task.get("planned_outputs"))
    asset_key = text_value(task.get("asset_key"))
    return text_value(planned.get(key)) or f"{STORYBOARD_WORKING_REL}/{asset_key}{fallback_suffix}"


def prompt_path_for(task: dict[str, Any]) -> str:
    return output_rel(task, "video_prompt_path", "_VideoPrompt.json")


def raw_path_for(task: dict[str, Any]) -> str:
    return output_rel(task, "raw_video_path", "_Video_Raw.mp4")


def final_path_for(task: dict[str, Any]) -> str:
    return output_rel(task, "final_video_path", "_Video_Final.mp4")


def tail_path_for(task: dict[str, Any]) -> str:
    return output_rel(task, "tail_frame_path", "_TailFrame.png")


def raw_or_final_exists(workspace: Path, task: dict[str, Any]) -> bool:
    return file_exists(workspace, raw_path_for(task)) or file_exists(workspace, final_path_for(task))


def task_executable(task: dict[str, Any]) -> bool:
    status = text_value(task.get("status"))
    return bool(text_value(task.get("asset_key")) and status != "skipped" and not status.startswith("blocked"))


def initial_execution_marker(args: Args, selected: list[dict[str, Any]]) -> dict[str, Any]:
    task = next((item for item in selected if task_executable(item)), selected[0] if selected else {})
    step = "video" if args.mode == "video-only" else "prompt"
    key = task_key(task) if task else ""
    return {
        "current_task_id": text_value(task.get("video_only_task_id")),
        "current_asset_key": text_value(task.get("asset_key")),
        "current_step": step if key else "",
        "current_step_status": "running_generate" if key else "",
        "segments": {
            key: {
                "video_only_task_id": text_value(task.get("video_only_task_id")),
                "asset_key": text_value(task.get("asset_key")),
                "steps": {step: {"status": "running_generate", "updated_at": now_iso()}},
            }
        } if key else {},
    }


def execution_state_base(args: Args, plan: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "analysis_v1_video_only_plan_execution_state_0.1",
        "job_id": f"vop_exec_{now_ms()}",
        "status": "queued",
        "mode": args.mode,
        "source_plan_hash": text_value(plan.get("plan_hash")),
        "summary": dict_value(result.get("summary")) if isinstance(result, dict) else {},
        "error": "",
        "started_at": now_iso(),
        "updated_at": now_iso(),
    }


def write_execution_state(workspace: Path, state: dict[str, Any]) -> None:
    write_json(workspace / EXECUTION_STATE_REL, state)
    write_json(workspace / SESSION_EXECUTION_STATE_REL, state)


def update_step_execution_state(workspace: Path, args: Args, plan: dict[str, Any], task: dict[str, Any], step_name: str, step_status: str, result: dict[str, Any], output_path: str = "", error: str = "") -> None:
    current = read_json_or_empty(workspace / SESSION_EXECUTION_STATE_REL)
    state = {
        **execution_state_base(args, plan, result),
        **(current if isinstance(current, dict) else {}),
        "status": "running",
        "mode": args.mode,
        "source_plan_hash": text_value(plan.get("plan_hash")),
        "current_task_id": text_value(task.get("video_only_task_id")),
        "current_asset_key": text_value(task.get("asset_key")),
        "current_step": step_name if step_status.startswith("running") else text_value((current if isinstance(current, dict) else {}).get("current_step")),
        "current_step_status": step_status,
        "updated_at": now_iso(),
    }
    segments = state.get("segments") if isinstance(state.get("segments"), dict) else {}
    key = task_key(task)
    segment_state = segments.get(key) if isinstance(segments.get(key), dict) else {}
    steps = segment_state.get("steps") if isinstance(segment_state.get("steps"), dict) else {}
    payload = {"status": step_status, "updated_at": now_iso()}
    if output_path:
        payload["output_path"] = output_path
    if error:
        payload["error"] = error
    steps[step_name] = {**(steps.get(step_name) if isinstance(steps.get(step_name), dict) else {}), **payload}
    segments[key] = {
        **segment_state,
        "video_only_task_id": text_value(task.get("video_only_task_id")),
        "asset_key": text_value(task.get("asset_key")),
        "steps": steps,
    }
    state["segments"] = segments
    write_execution_state(workspace, state)


def source_segment(task: dict[str, Any]) -> dict[str, Any]:
    segment = dict_value(task.get("source_segment"))
    dialogue_asset_keys = list_value(task.get("dialogue_asset_keys")) or list_value(task.get("dialogue_ids"))
    if segment:
        return segment
    fallback = {
        "segment_id": text_value(task.get("source_video_plan_segment_id")),
        "dialogue_asset_keys": dialogue_asset_keys,
        "dialogue_ids": dialogue_asset_keys,
        "planned_outputs": dict_value(task.get("planned_outputs")),
    }
    for key in (
        "video_generation_mode",
        "provider",
        "model",
        "model_alias",
        "reference_mode",
        "prompt_template",
        "reference_video_path",
        "source_face_masked_reference_video_path",
        "reference_video_role",
        "storyboard_seed_segment_id",
        "storyboard_seed_path",
    ):
        if text_value(task.get(key)):
            fallback[key] = task.get(key)
    nested = dict_value(task.get("dance_mimic"))
    if nested:
        fallback["dance_mimic"] = nested
    return fallback


def talking_head_privacy_segment(storyboard: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    reference = dict_value(segment.get("talking_head_reference"))
    if not reference:
        talking_head_config = dict_value(storyboard.get("talking_head_config"))
        reference = dict_value(talking_head_config.get("max_sd_2_reference"))
    if not reference:
        return segment
    if not reference.get("privacy_grid_mode") or not reference.get("target_identity_grid_applied"):
        return segment
    return {**segment, "talking_head_reference": reference}


def apply_talking_head_first_frame_privacy(
    workspace: Path,
    variables: dict[str, Any],
    storyboard: dict[str, Any],
    segment: dict[str, Any],
    first_frame_path: Path,
    working_dir: Path,
    asset_key: str,
    result: dict[str, Any],
) -> Path:
    if not VPE.should_apply_max_sd_2_oral_privacy_grid(variables, storyboard, segment):
        return first_frame_path
    privacy_segment = talking_head_privacy_segment(storyboard, segment)
    if not dict_value(privacy_segment.get("talking_head_reference")):
        return first_frame_path
    try:
        output, metadata = talking_head_privacy_grid_module().prepare_continuity_frame(
            workspace,
            variables,
            privacy_segment,
            first_frame_path,
            working_dir,
            asset_key,
        )
    except Exception as exc:
        raise ToolError(f"talking_head_privacy_grid_continuity_failed: {exc}") from exc
    if metadata:
        result.setdefault("continuity_privacy_grid", {})[asset_key] = metadata
    return output


def source_plan_for_video(plan: dict[str, Any]) -> dict[str, Any]:
    return {"consistency_references": dict_value(dict_value(plan.get("source_video_plan")).get("consistency_references"))}


def write_prompt_variables(prompt_dir: Path, asset_key: str, context: dict[str, Any]) -> Path:
    path = prompt_dir / f"PromptVariables_{asset_key}_Video.json"
    write_json(path, {
        "schema_version": "analysis_v1_05_06_video_prompt_variables_0.1",
        "asset_key": asset_key,
        "segment": context.get("segment"),
        "shot": context.get("shot"),
        "scene": context.get("scene"),
    })
    return path


def build_video_prompt(workspace: Path, variables: dict[str, Any], storyboard: dict[str, Any], plan: dict[str, Any], task: dict[str, Any], args: Args, result: dict[str, Any]) -> Path:
    del storyboard
    asset_key = text_value(task.get("asset_key"))
    prompt_dir = workspace / TOOL_DIR_NAME / "Prompt"
    segment = source_segment(task)
    video_selection = VPE.video_selection_for_segment(variables, args, segment)
    video_module = VPE.video_module_for(video_selection.get("provider", ""), video_selection.get("model", ""))
    planned_first_frame = workspace_path(workspace, output_rel(task, "first_frame_path", "_Image_New.png"))
    first_frame = dict_value(segment.get("first_frame"))
    materialize = dict_value(first_frame.get("materialize_first_frame"))
    source_rel = text_value(materialize.get("copy_from_path")) if materialize.get("required") else text_value(first_frame.get("source_path"))
    aspect_source = planned_first_frame if planned_first_frame.exists() else workspace_path(workspace, source_rel) if source_rel else None
    target_aspect = "9:16"
    if aspect_source is not None and aspect_source.exists() and aspect_source.is_file():
        try:
            _width, _height, target_aspect = VPE.inspect_video_first_frame(aspect_source)
        except Exception:
            # Prompt-only execution must remain usable for legacy plans whose
            # first-frame placeholder has not been materialized as an image yet.
            target_aspect = "9:16"
    context = {
        "workspace": str(workspace),
        "prompt_dir": str(prompt_dir),
        "reference_images": [str(aspect_source)] if aspect_source is not None else [],
        "segment": segment,
        "shot": dict_value(task.get("source_shot")),
        "scene": dict_value(task.get("source_scene")),
        "dialogue_index": VPE.flatten_dialogues(load_required_json(workspace, STORYBOARD_REL, "storyboard_missing")),
        "prompt_template": text_value(video_selection.get("prompt_template")),
        "aspect": target_aspect,
        "aspect_ratio": target_aspect,
        "requested_aspect": target_aspect,
    }
    write_prompt_variables(prompt_dir, asset_key, context)
    package = VPE.prompt_package_for_video_aspect(video_module.build_prompt_package(context), target_aspect)
    package.update({
        "video_only_task_id": text_value(task.get("video_only_task_id")),
        "asset_key": asset_key,
        "prompt_status": "draft_generated",
        "prompt_origin": "system_generated",
        "prompt_revision": 1,
        "source_plan_hash": text_value(plan.get("plan_hash")),
        "updated_at": now_iso(),
    })
    rendered_path = video_module.write_prompt_package(prompt_dir, asset_key, package)
    business_prompt = prompt_path_for(task)
    if workspace_path(workspace, business_prompt).exists() and not args.overwrite_prompt:
        return workspace_path(workspace, business_prompt)
    published = publish_file(workspace, rendered_path, business_prompt, result)
    return workspace_path(workspace, published)


def prompt_snapshot_for_video(workspace: Path, task: dict[str, Any], result: dict[str, Any]) -> Path:
    business_prompt = workspace_path(workspace, prompt_path_for(task))
    if not business_prompt.exists():
        raise ToolError(f"Video prompt is missing for video-only execution: {prompt_path_for(task)}")
    snapshot = workspace / TOOL_DIR_NAME / "Prompt" / f"PromptSnapshot_{text_value(task.get('asset_key'))}_VideoPrompt.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if business_prompt.resolve() != snapshot.resolve():
        shutil.copy2(business_prompt, snapshot)
    append_created_file(result, rel(workspace, snapshot))
    return snapshot


def prepare_segment_audio(workspace: Path, args: Args, variables: dict[str, Any], storyboard: dict[str, Any], task: dict[str, Any], result: dict[str, Any]) -> Path:
    asset_key = text_value(task.get("asset_key"))
    segment = source_segment(task)
    planned_rel = output_rel(task, "segment_audio_path", "_SegmentAudio_Final.wav")
    existing = workspace_path(workspace, planned_rel)
    working = workspace / TOOL_DIR_NAME / "Working" / f"{asset_key}_SegmentAudio_Final.wav"
    if existing.exists() and existing.stat().st_size > 0:
        shutil.copy2(existing, working)
        append_created_file(result, rel(workspace, working))
        return working
    dialogue_index = VPE.flatten_dialogues(storyboard)
    audio_files: list[Path] = []
    for audio_task in list_value(segment.get("dialogue_audio_tasks")):
        if not isinstance(audio_task, dict):
            continue
        dialogue_asset_key = text_value(audio_task.get("dialogue_asset_key"))
        if not dialogue_asset_key:
            raise ToolError("Dialogue audio task is missing dialogue_asset_key.")
        existing_audio = text_value(audio_task.get("existing_audio_path"))
        if existing_audio and workspace_path(workspace, existing_audio).exists():
            audio_file = VPE.copy_to_working(workspace, existing_audio, f"{VPE.safe_name(dialogue_asset_key, asset_key)}_DialogueAudio.wav")
        elif audio_task.get("need_audio"):
            dialogue = dict_value(dialogue_index.get(dialogue_asset_key, {}).get("dialogue"))
            tts_config = VPE.load_provider_config(args, variables, "tts", args.tts_provider, args.tts_model)
            tts_config = {**tts_config, "voice": text_value(dict_value(variables.get("gemini_builder_g_config")).get("voice") or "Aoede")}
            audio_file = workspace / TOOL_DIR_NAME / "Working" / f"{VPE.safe_name(dialogue_asset_key, asset_key)}_Audio_Generated.wav"
            prompt_text = VPE.tts_prompt_for_dialogue(dialogue)
            VPE.record_model_call(workspace / TOOL_DIR_NAME / "Prompt", VPE.safe_name(dialogue_asset_key, asset_key), "TTS", {"provider_config": VPE.redact_config(tts_config), "prompt": prompt_text})
            tts_response = VPE.generate_tts_with_provider(tts_config, prompt_text, audio_file, args.provider_timeout_seconds)
            VPE.record_model_call(workspace / TOOL_DIR_NAME / "Prompt", VPE.safe_name(dialogue_asset_key, asset_key), "TTS", {"provider_config": VPE.redact_config(tts_config), "prompt": prompt_text}, tts_response)
            planned_audio_rel = text_value(audio_task.get("planned_audio_path"))
            if planned_audio_rel:
                publish_file(workspace, audio_file, planned_audio_rel, result)
        else:
            raise ToolError(f"Dialogue audio is missing and not planned for generation: {dialogue_asset_key}.")
        audio_files.append(audio_file)
    audio_result = VPE.compose_segment_audio(workspace, audio_files, working)
    publish_file(workspace, working, planned_rel, result)
    result.setdefault("audio", audio_result)
    return working


def prepare_first_frame(workspace: Path, args: Args, variables: dict[str, Any], storyboard: dict[str, Any], plan: dict[str, Any], task: dict[str, Any], result: dict[str, Any]) -> Path:
    asset_key = text_value(task.get("asset_key"))
    segment = source_segment(task)
    planned_rel = output_rel(task, "first_frame_path", "_Image_New.png")
    existing = workspace_path(workspace, planned_rel)
    if existing.exists() and existing.stat().st_size > 0:
        copied = workspace / TOOL_DIR_NAME / "Working" / f"{asset_key}_FirstFrame{existing.suffix or '.png'}"
        shutil.copy2(existing, copied)
        append_created_file(result, rel(workspace, copied))
        privacy_output = apply_talking_head_first_frame_privacy(
            workspace,
            variables,
            storyboard,
            segment,
            copied,
            workspace / TOOL_DIR_NAME / "Working",
            asset_key,
            result,
        )
        normalization = VPE.normalize_video_first_frame(privacy_output)
        result.setdefault("first_frame_normalization", {})[asset_key] = normalization
        # Image_New is the StoryBoard contract for the generated video frame.
        # Publish the privacy-processed and cropped copy back to that slot so its visible media and
        # the provider input have exactly the same canonical aspect ratio.
        publish_file(workspace, privacy_output, planned_rel, result)
        bind_first_frame_to_storyboard(workspace, storyboard, task, planned_rel, "", result)
        return privacy_output
    first_frame = dict_value(segment.get("first_frame"))
    materialize = dict_value(first_frame.get("materialize_first_frame"))
    copy_from = text_value(materialize.get("copy_from_path")) if materialize.get("required") else ""
    if copy_from and workspace_path(workspace, copy_from).exists():
        copied = VPE.copy_to_working(workspace, copy_from, f"{asset_key}_FirstFrame{Path(copy_from).suffix or '.png'}")
        privacy_output = apply_talking_head_first_frame_privacy(
            workspace,
            variables,
            storyboard,
            segment,
            copied,
            workspace / TOOL_DIR_NAME / "Working",
            asset_key,
            result,
        )
        normalization = VPE.normalize_video_first_frame(privacy_output)
        result.setdefault("first_frame_normalization", {})[asset_key] = normalization
        publish_file(workspace, privacy_output, planned_rel, result)
        bind_first_frame_to_storyboard(workspace, storyboard, task, planned_rel, "tail_frame_materialized", result)
        return privacy_output
    if not (dict_value(segment.get("tasks")).get("need_image_prompt") or dict_value(segment.get("tasks")).get("need_image")):
        raise ToolError(f"First frame is missing for video generation: {asset_key}")
    image_refs = VPE.prepare_image_references(workspace, segment, source_plan_for_video(plan))
    image_paths = [workspace_path(workspace, item["working_path"]) for item in image_refs if text_value(item.get("working_path"))]
    target_ref = VPE.reference_by_kind(image_refs, "target_frame")
    target_frame_path = workspace_path(workspace, text_value(target_ref.get("working_path"))) if text_value(target_ref.get("working_path")) else None
    image_selection, assessment = VPE.image_provider_selection_for_references(args, variables, image_refs)
    image_module = VPE.image_module_for(image_selection.get("provider", ""), image_selection.get("model", ""))
    prompt_context = {
        "workspace": str(workspace),
        "prompt_dir": str(workspace / TOOL_DIR_NAME / "Prompt"),
        "segment": segment,
        "shot": dict_value(task.get("source_shot")),
        "scene": dict_value(task.get("source_scene")),
        "dialogue_index": VPE.flatten_dialogues(load_required_json(workspace, STORYBOARD_REL, "storyboard_missing")),
        "references": image_refs,
        "reference_manifests": {},
    }
    image_prompt = image_module.build_prompt_package(prompt_context)
    if assessment:
        image_prompt["provider_reference_assessment"] = assessment
    image_prompt_working = image_module.write_prompt_package(workspace / TOOL_DIR_NAME / "Prompt", asset_key, image_prompt)
    image_output = workspace / TOOL_DIR_NAME / "Working" / f"{asset_key}_Image_New.png"
    image_config = VPE.load_provider_config(args, variables, "image", image_selection["provider"], image_selection["model"])
    VPE.record_model_call(workspace / TOOL_DIR_NAME / "Prompt", asset_key, "Image", {"provider_config": VPE.redact_config(image_config), "prompt_path": rel(workspace, image_prompt_working), "reference_paths": [rel(workspace, path) for path in image_paths]})
    image_response = VPE.generate_image_with_provider(image_config, image_prompt_working, image_output, image_paths, args.provider_timeout_seconds)
    image_response["dimension_normalization"] = VPE.normalize_image_to_target_aspect(image_output, target_frame_path)
    image_output = apply_talking_head_first_frame_privacy(
        workspace,
        variables,
        storyboard,
        segment,
        image_output,
        workspace / TOOL_DIR_NAME / "Working",
        asset_key,
        result,
    )
    normalization = VPE.normalize_video_first_frame(image_output)
    result.setdefault("first_frame_normalization", {})[asset_key] = normalization
    image_response["video_first_frame_normalization"] = normalization
    VPE.record_model_call(workspace / TOOL_DIR_NAME / "Prompt", asset_key, "Image", {"provider_config": VPE.redact_config(image_config), "prompt_path": rel(workspace, image_prompt_working), "reference_paths": [rel(workspace, path) for path in image_paths]}, image_response)
    publish_file(workspace, image_output, planned_rel, result)
    bind_first_frame_to_storyboard(workspace, storyboard, task, planned_rel, "generated", result)
    return image_output


def execute_video(workspace: Path, args: Args, variables: dict[str, Any], storyboard: dict[str, Any], plan: dict[str, Any], task: dict[str, Any], result: dict[str, Any]) -> str:
    asset_key = text_value(task.get("asset_key"))
    if file_exists(workspace, final_path_for(task)):
        raise ToolError(f"Final video already exists for {asset_key}; clear Final/Raw before regenerating Video.")
    if file_exists(workspace, raw_path_for(task)) and not args.overwrite_video:
        raise ToolError(f"Raw video already exists for {asset_key}; confirm Final or clear Raw before regenerating Video.")
    segment_audio = prepare_segment_audio(workspace, args, variables, storyboard, task, result)
    first_frame = prepare_first_frame(workspace, args, variables, storyboard, plan, task, result)
    _first_frame_width, _first_frame_height, target_video_aspect = VPE.inspect_video_first_frame(first_frame)
    VPE.rewrite_video_prompt_file(workspace_path(workspace, prompt_path_for(task)), target_video_aspect)
    prompt_snapshot = prompt_snapshot_for_video(workspace, task, result)
    segment = source_segment(task)
    video_selection = VPE.video_selection_for_segment(variables, args, segment)
    video_config = VPE.load_video_provider_config_for_segment(args, variables, segment)
    requested_duration = VPE.safe_float(dict_value(task.get("planned_outputs")).get("video_duration_seconds"), VPE.safe_float(segment.get("planned_video_duration"), 4.0))
    audio_duration_seconds = VPE.media_duration_seconds(segment_audio) if segment_audio.exists() else 0.0
    provider_duration = VPE.provider_video_seconds(video_config, requested_duration, audio_duration_seconds)
    working_dir = workspace / TOOL_DIR_NAME / "Working"
    raw_working = working_dir / f"{asset_key}_Video_Raw.mp4"
    provider_task_state_path = working_dir / f"{asset_key}_Video_ProviderTask.json"
    reference_image_source = task if VPE.is_dance_mimic_reference_video_segment(task) else segment
    reference_images, reference_image_roles = VPE.dance_mimic_video_reference_images(workspace, reference_image_source, first_frame, {})
    reference_videos = VPE.prepare_dance_mimic_reference_videos(workspace, task, working_dir, asset_key, result)
    if not reference_videos:
        reference_videos = VPE.prepare_max_sd_2_reference_videos(
            workspace,
            task,
            video_selection,
            working_dir,
            asset_key,
            result,
        )
    video_request = {
        "provider_config": VPE.redact_config(video_config),
        "prompt_path": rel(workspace, prompt_snapshot),
        "first_frame": rel(workspace, first_frame),
        "reference_images": [rel(workspace, path) for path in reference_images],
        "reference_image_roles": reference_image_roles,
        "requested_duration_seconds": requested_duration,
        "audio_duration_seconds": round(audio_duration_seconds, 3) if audio_duration_seconds else None,
        "provider_duration_seconds": provider_duration,
        "provider_task_state_path": rel(workspace, provider_task_state_path),
        "aspect_ratio": target_video_aspect,
        "first_frame_normalization": dict_value(result.get("first_frame_normalization")).get(asset_key) or {},
    }
    if VPE.is_wan_rtv_model(text_value(video_config.get("provider")), text_value(video_config.get("model"))):
        video_request["reference_video"] = rel(workspace, workspace / TOOL_DIR_NAME / "Working" / VPE.WAN_RTV_REFERENCE_VIDEO_NAME)
        video_request["provider_size"] = VPE.wan_rtv_video_size_for_image(first_frame, video_config)
    if VPE.is_kling_omni_model(text_value(video_config.get("provider")), text_value(video_config.get("model"))):
        video_request["reference_video"] = rel(workspace, workspace / TOOL_DIR_NAME / "Working" / VPE.KLING_OMNI_REFERENCE_VIDEO_NAME)
    if reference_videos:
        video_request["reference_video"] = rel(workspace, reference_videos[0])
        video_request["reference_videos"] = [rel(workspace, path) for path in reference_videos]
        video_request["reference_video_count"] = len(reference_videos)
        video_request["reference_mode"] = text_value(video_selection.get("reference_mode") or video_config.get("reference_mode"))
        video_request["video_generation_mode"] = text_value(video_selection.get("video_generation_mode") or video_config.get("video_generation_mode"))
        video_request["reference_video_role"] = text_value(
            video_selection.get("reference_video_role")
            or video_config.get("reference_video_role")
            or (VPE.DANCE_MIMIC_REFERENCE_VIDEO_ROLE if VPE.is_dance_mimic_reference_video_segment(reference_image_source) else "")
        )
    VPE.record_model_call(workspace / TOOL_DIR_NAME / "Prompt", asset_key, "Video", video_request)
    video_response = VPE.generate_video_with_provider(
        video_config,
        prompt_snapshot,
        raw_working,
        reference_images,
        requested_duration,
        args.provider_timeout_seconds,
        provider_task_state_path,
        audio_duration_seconds,
        reference_videos=reference_videos,
        requested_aspect=target_video_aspect,
    )
    VPE.record_model_call(workspace / TOOL_DIR_NAME / "Prompt", asset_key, "Video", video_request, video_response)
    raw_rel = publish_file(workspace, raw_working, raw_path_for(task), result)
    tail_rel = tail_path_for(task)
    tail_suffix = Path(tail_rel).suffix or ".png"
    tail_working = workspace / TOOL_DIR_NAME / "Working" / f"{asset_key}_TailFrame{tail_suffix}"
    tail_result = VPE.extract_tail_frame(raw_working, tail_working)
    publish_file(workspace, tail_working, tail_rel, result)
    result.setdefault("model_calls", {}).setdefault(asset_key, {})["video"] = {"config": VPE.redact_config(video_config), "response": video_response}
    result.setdefault("tail_frames", {})[asset_key] = tail_result
    return raw_rel


def execute_task(workspace: Path, variables: dict[str, Any], storyboard: dict[str, Any], plan: dict[str, Any], task: dict[str, Any], args: Args, result: dict[str, Any]) -> dict[str, Any]:
    asset_key = text_value(task.get("asset_key"))
    task_result: dict[str, Any] = {
        "video_only_task_id": text_value(task.get("video_only_task_id")),
        "asset_key": asset_key,
        "status": "completed",
        "steps": {},
        "outputs": {},
        "error": "",
    }
    if not task_executable(task):
        task_result["status"] = "blocked" if text_value(task.get("status")).startswith("blocked") else "skipped"
        task_result["error"] = text_value(task.get("blocked_reason"))
        return task_result
    active_step = "prompt"
    try:
        if raw_or_final_exists(workspace, task) and args.mode in {"prompt-only", "prompt-and-video"}:
            raise ToolError(f"Raw or Final already exists for {asset_key}; Prompt cannot be regenerated.")
        if args.mode in {"prompt-only", "prompt-and-video"}:
            active_step = "prompt"
            update_step_execution_state(workspace, args, plan, task, "prompt", "running_generate", result)
            prompt_path = build_video_prompt(workspace, variables, storyboard, plan, task, args, result)
            prompt_rel = rel(workspace, prompt_path)
            task_result["steps"]["prompt"] = {"status": "completed_working", "output_path": prompt_rel}
            task_result["outputs"]["video_prompt_path"] = prompt_rel
            update_step_execution_state(workspace, args, plan, task, "prompt", "completed_working", result, prompt_rel)
        if args.mode in {"video-only", "prompt-and-video"}:
            active_step = "video"
            update_step_execution_state(workspace, args, plan, task, "video", "running_generate", result)
            raw_rel = execute_video(workspace, args, variables, storyboard, plan, task, result)
            task_result["steps"]["video"] = {"status": "completed_working", "output_path": raw_rel}
            task_result["outputs"]["raw_video_path"] = raw_rel
            update_step_execution_state(workspace, args, plan, task, "video", "completed_working", result, raw_rel)
    except Exception as exc:
        task_result["status"] = "failed"
        task_result["error"] = str(exc)
        update_step_execution_state(workspace, args, plan, task, active_step, "failed", result, error=str(exc))
    return task_result


def summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "task_count": len(results),
        "completed_count": sum(1 for item in results if item.get("status") == "completed"),
        "skipped_count": sum(1 for item in results if item.get("status") == "skipped"),
        "blocked_count": sum(1 for item in results if item.get("status") == "blocked"),
        "failed_count": sum(1 for item in results if item.get("status") == "failed"),
        "prompt_completed_count": sum(1 for item in results if dict_value(item.get("steps")).get("prompt", {}).get("status") == "completed_working"),
        "video_completed_count": sum(1 for item in results if dict_value(item.get("steps")).get("video", {}).get("status") == "completed_working"),
    }


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "mode": args.mode,
        "requires_database": args.mode in {"video-only", "prompt-and-video"},
        "requires_model_calls": args.mode in {"video-only", "prompt-and-video"},
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
        if args.mode not in {"prompt-only", "video-only", "prompt-and-video"}:
            raise ToolError("--mode must be prompt-only, video-only, or prompt-and-video")
        if args.force:
            force_reset(workspace, result)
        ensure_tool_dirs(workspace)
        variables = load_required_json(workspace, VARIABLES_REL, "variables_missing")
        storyboard = load_required_json(workspace, STORYBOARD_REL, "storyboard_missing")
        plan = load_required_json(workspace, PLAN_REL, "video_only_plan_missing")
        source_plan_hash = text_value(args.source_plan_hash or plan.get("plan_hash"))
        plan = {**plan, "plan_hash": source_plan_hash}
        result["source_plan_hash"] = source_plan_hash
        copy_inputs_to_working(workspace, variables, storyboard, plan, result, args)
        selected = select_tasks(plan, args)
        if not selected:
            raise ToolError("video_only_plan_has_no_selected_tasks")
        previous_state = read_json_or_empty(workspace / SESSION_EXECUTION_STATE_REL)
        initial_state = execution_state_base(args, plan, result)
        initial_state.update({
            **(previous_state if isinstance(previous_state, dict) else {}),
            "job_id": f"vop_exec_{now_ms()}_{uuid.uuid4().hex[:8]}",
            "status": "queued",
            "target_task_id": args.target_task_id,
            "target_asset_key": args.target_asset_key,
            **initial_execution_marker(args, selected),
        })
        write_execution_state(workspace, initial_state)
        task_results = [execute_task(workspace, variables, storyboard, plan, task, args, result) for task in selected]
        result["tasks"] = task_results
        result["summary"] = summarize(task_results)
        if result["summary"]["failed_count"]:
            result["status"] = "completed_with_failed_items" if result["summary"]["completed_count"] else "failed"
        elif result["summary"]["blocked_count"]:
            result["status"] = "completed_with_blocked_items"
        write_json(workspace / EXECUTION_RESULT_REL, result)
        write_json(workspace / SESSION_EXECUTION_RESULT_REL, result)
        state = read_json_or_empty(workspace / SESSION_EXECUTION_STATE_REL)
        state = {
            **(state if isinstance(state, dict) else {}),
            "schema_version": "analysis_v1_video_only_plan_execution_state_0.1",
            "status": result["status"],
            "mode": args.mode,
            "current_task_id": "",
            "current_asset_key": "",
            "current_step": "",
            "current_step_status": "",
            "source_plan_hash": source_plan_hash,
            "summary": result["summary"],
            "updated_at": now_iso(),
        }
        write_execution_state(workspace, state)
        warnings = scan_for_sensitive_output(result)
        result.setdefault("warnings", []).extend(warnings)
        if warnings:
            result["status"] = "failed"
            result.setdefault("blocked_reasons", []).append({"code": "sensitive_output_detected", "message": "Sensitive-looking content detected in tool output."})
    except Exception as exc:
        ensure_tool_dirs(workspace) if workspace.exists() and workspace.is_dir() else None
        result["status"] = "blocked" if isinstance(exc, ToolError) else "failed"
        result.setdefault("blocked_reasons", []).append({"code": "execution_error", "message": str(exc)})
        state = read_json_or_empty(workspace / SESSION_EXECUTION_STATE_REL)
        if isinstance(state, dict):
            state = {**state, "status": result["status"], "error": str(exc), "updated_at": now_iso()}
            write_execution_state(workspace, state)
    result["updated_at"] = now_iso()
    if workspace.exists() and workspace.is_dir():
        write_json(workspace / RESULT_REL, result)
    return result


def parse_args(argv: list[str] | None = None) -> Args:
    parser = argparse.ArgumentParser(description="Execute Analysis_V1 video-only prompt/video steps.")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--mode", choices=("prompt-only", "video-only", "prompt-and-video"), default="prompt-only")
    parser.add_argument("--image-provider", default="")
    parser.add_argument("--image-model", default="")
    parser.add_argument("--video-provider", default="")
    parser.add_argument("--video-model", default="")
    parser.add_argument("--tts-provider", default="")
    parser.add_argument("--tts-model", default="")
    parser.add_argument("--source-plan-hash", default="")
    parser.add_argument("--target-task-id", default="")
    parser.add_argument("--target-asset-key", default="")
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--overwrite-prompt", action="store_true")
    parser.add_argument("--overwrite-video", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--provider-timeout-seconds", type=int, default=7200)
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
