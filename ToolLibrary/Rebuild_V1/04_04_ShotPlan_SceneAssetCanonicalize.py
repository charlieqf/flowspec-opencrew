from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


TOOL_ID = "04_04_ShotPlan_SceneAssetCanonicalize"
TOOL_NAME = "ShotPlan Scene Asset Canonicalize"
TOOL_VERSION = "1.0.0"
REQUIRES = ["rebuild_shot_plan.json"]
PRODUCES = [
    "rebuild_shot_plan.json",
    "scene_marks.json",
    "asset_tasks.json",
    "asset_prompts_shot_*.json",
]
SUGGESTED_PREVIOUS_TOOLS = ["04_03_ShotPlan_FirstLastReadinessCheck"]
SUGGESTED_NEXT_TOOLS = ["05_01_ShotPlan_ScenePromptRefresh"]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in value).strip("_") or "item"


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (plan.get("shots") or []) if isinstance(item, dict)]


def keyframes_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    frames = [item for item in (reference.get("keyframes") or []) if isinstance(item, dict)]
    return sorted(frames, key=lambda item: (float(item.get("time") or 1_000_000), str(item.get("path") or "")))


def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    return [item for item in (reference.get("scene_marks") or []) if isinstance(item, dict)]


def canonicalize_plan(plan: dict[str, Any]) -> tuple[dict[str, dict[str, str]], set[tuple[str, str]]]:
    scene_id_maps: dict[str, dict[str, str]] = {}
    valid_scene_keys: set[tuple[str, str]] = set()
    for shot in shot_list(plan):
        shot_id = str(shot.get("shot_id") or "")
        if not shot_id:
            continue
        reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
        frames = keyframes_for_shot(shot)
        frame_order = {str(frame.get("path") or ""): index for index, frame in enumerate(frames)}
        marks = [dict(mark) for mark in scene_marks_for_shot(shot)]

        def sort_key(mark: dict[str, Any]) -> tuple[int, float, str]:
            keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
            paths = [str(keyframes.get(key) or "") for key in ("single", "first", "last")]
            first_order = min([frame_order[path] for path in paths if path in frame_order] or [1_000_000])
            try:
                start = float(mark.get("start"))
            except (TypeError, ValueError):
                start = 1_000_000.0
            return (first_order, start, str(mark.get("scene_mark_id") or ""))

        marks.sort(key=sort_key)
        scene_id_map: dict[str, str] = {}
        for index, mark in enumerate(marks, start=1):
            old_id = str(mark.get("scene_mark_id") or "")
            new_id = f"{shot_id}_scene_{index:03d}"
            if old_id:
                scene_id_map[old_id] = new_id
            mark["scene_mark_id"] = new_id
            mark["shot_id"] = shot_id
            mark["scene_index"] = index
            generation_mode = "first_last" if str(mark.get("generation_mode") or mark.get("asset_generation_mode") or "first_frame") == "first_last" else "first_frame"
            mark["generation_mode"] = generation_mode
            asset_state = mark.get("asset_state") if isinstance(mark.get("asset_state"), dict) else {}
            scene_asset = asset_state.get("scene_asset") if isinstance(asset_state.get("scene_asset"), dict) else {}
            scene_asset["uses_only_first_frame"] = generation_mode != "first_last"
            asset_state["scene_asset"] = scene_asset
            mark["asset_state"] = asset_state
            valid_scene_keys.add((shot_id, new_id))
        reference["scene_marks"] = marks
        summary = reference.get("scene_mark_summary") if isinstance(reference.get("scene_mark_summary"), dict) else {}
        next_summary = {**summary, "scene_id_mode": "canonical"}
        next_summary.pop("scene_id_map", None)
        reference["scene_mark_summary"] = next_summary
        shot["reference"] = reference
        for frame in frames:
            scene_mark = frame.get("scene_mark") if isinstance(frame.get("scene_mark"), dict) else None
            if not scene_mark:
                continue
            old_id = str(scene_mark.get("scene_mark_id") or "")
            new_id = scene_id_map.get(old_id, old_id)
            if (shot_id, new_id) in valid_scene_keys:
                scene_mark["scene_mark_id"] = new_id
                scene_mark["scene_index"] = next((mark["scene_index"] for mark in marks if mark["scene_mark_id"] == new_id), scene_mark.get("scene_index"))
            else:
                frame.pop("scene_mark", None)
        reference["keyframes"] = frames
        if any(old != new for old, new in scene_id_map.items()):
            scene_id_maps[shot_id] = scene_id_map
    return scene_id_maps, valid_scene_keys


def update_scene_ids(value: Any, scene_id_maps: dict[str, dict[str, str]]) -> Any:
    if isinstance(value, dict):
        shot_id = str(value.get("shot_id") or "")
        mapped = {key: update_scene_ids(item, scene_id_maps) for key, item in value.items()}
        scene_id = str(mapped.get("scene_mark_id") or "")
        if shot_id and scene_id in scene_id_maps.get(shot_id, {}):
            mapped["scene_mark_id"] = scene_id_maps[shot_id][scene_id]
        return mapped
    if isinstance(value, list):
        return [update_scene_ids(item, scene_id_maps) for item in value]
    return value


def json_files_to_update(workspace: Path) -> list[Path]:
    files = [workspace / "scene_marks.json", workspace / "asset_tasks.json"]
    files.extend(workspace.glob("asset_prompts_shot_*.json"))
    for root in ("asset_image_workflows", "asset_video_workflows", "asset_tts_workflows"):
        base = workspace / root
        if base.exists():
            files.extend(base.glob("**/workflow.json"))
    return [path for path in files if path.exists() and path.is_file()]


def stale_asset_dirs(workspace: Path, valid_scene_keys: set[tuple[str, str]]) -> list[Path]:
    stale: list[Path] = []
    assets = workspace / "assets"
    if not assets.exists():
        return stale
    valid_by_shot: dict[str, set[str]] = {}
    for shot_id, scene_id in valid_scene_keys:
        valid_by_shot.setdefault(shot_id, set()).add(scene_id)
    valid_shots = set(valid_by_shot)
    for variant_dir in [item for item in assets.iterdir() if item.is_dir() and item.name.startswith("variant_")]:
        for shot_dir in [item for item in variant_dir.iterdir() if item.is_dir()]:
            if shot_dir.name not in valid_shots:
                stale.append(shot_dir)
                continue
            valid_scenes = valid_by_shot[shot_dir.name]
            for scene_dir in [item for item in shot_dir.iterdir() if item.is_dir()]:
                is_scene_dir = scene_dir.name.startswith(f"{shot_dir.name}_scene_") or "_scene_manual_" in scene_dir.name
                if is_scene_dir and scene_dir.name not in valid_scenes:
                    stale.append(scene_dir)
    return stale


def stale_workflow_dirs(workspace: Path) -> list[Path]:
    stale: list[Path] = []
    for root in ("asset_image_workflows", "asset_video_workflows", "asset_tts_workflows"):
        base = workspace / root
        if not base.exists():
            continue
        stale.extend([item for item in base.iterdir() if item.is_dir() and "_scene_manual_" in item.name])
    return stale


def check_dependencies(workspace: Path) -> dict[str, Any]:
    plan_path = workspace / "rebuild_shot_plan.json"
    missing = []
    satisfied = []
    if plan_path.exists():
        satisfied.append("rebuild_shot_plan.json")
    else:
        missing.append({
            "dependency": "rebuild_shot_plan.json",
            "reason": "required workspace file does not exist: rebuild_shot_plan.json",
            "suggested_tools": ["02_RebuildShotPlanBuilder"],
            "scope": {},
        })
    return {
        "status": "blocked" if missing else "satisfied",
        "satisfied": satisfied,
        "missing": missing,
        "warnings": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Rebuild_V1 tool: 04_04_ShotPlan_SceneAssetCanonicalize")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    dependencies = check_dependencies(workspace)
    if args.check_dependencies_only or (dependencies["missing"] and not args.force):
        report = {
            "tool": TOOL_ID,
            "tool_version": TOOL_VERSION,
            "status": "blocked" if dependencies["missing"] else "completed",
            "workspace": str(workspace),
            "dependencies": dependencies,
            "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS,
            "suggested_next_tools": SUGGESTED_NEXT_TOOLS,
            "result": None,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["status"] == "blocked":
            raise SystemExit(2)
        return
    plan_path = workspace / "rebuild_shot_plan.json"
    plan = read_json(plan_path)
    scene_id_maps, valid_scene_keys = canonicalize_plan(plan)
    json_paths = json_files_to_update(workspace)
    remove_paths = stale_asset_dirs(workspace, valid_scene_keys) + stale_workflow_dirs(workspace)
    result = {
        "workspace": str(workspace),
        "apply": args.apply,
        "scene_id_maps": scene_id_maps,
        "json_files": [str(path.relative_to(workspace)) for path in json_paths],
        "remove_paths": [str(path.relative_to(workspace)) for path in remove_paths],
    }
    report = {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace": str(workspace),
        "dependencies": dependencies,
        "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS,
        "suggested_next_tools": SUGGESTED_NEXT_TOOLS,
        "result": result,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.apply:
        return
    write_json(plan_path, plan)
    for path in json_paths:
        try:
            write_json(path, update_scene_ids(read_json(path), scene_id_maps))
        except Exception:
            continue
    for path in sorted(remove_paths, key=lambda item: len(item.parts), reverse=True):
        if path.exists() and path.is_dir():
            shutil.rmtree(path)


if __name__ == "__main__":
    main()
