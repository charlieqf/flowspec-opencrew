from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any


TOOL_ID = "04_01_ShotPlan_FirstLastFrameMark"
TOOL_NAME = "ShotPlan First Last Frame Mark"
TOOL_VERSION = "1.0.0"
REQUIRES = ["rebuild_shot_plan.json", "saved_shot_keyframes"]
PRODUCES = ["rebuild_shot_plan.json", f"reports/rebuild_v1/{TOOL_ID}.json"]
SUGGESTED_PREVIOUS_TOOLS = ["03_04_ShotPlan_PreDeleteReadinessCheck"]
SUGGESTED_NEXT_TOOLS = ["04_02_ShotPlan_FirstLastFrameConfirm"]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("shots", []) if isinstance(item, dict)]


def keyframes_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    reference = shot.setdefault("reference", {})
    frames = [item for item in reference.get("keyframes", []) if isinstance(item, dict) and str(item.get("path") or "")]
    return sorted(frames, key=lambda item: (safe_float(item.get("time"), 1_000_000), str(item.get("path") or "")))


def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    reference = shot.setdefault("reference", {})
    marks = [item for item in reference.get("scene_marks", []) if isinstance(item, dict)]
    reference["scene_marks"] = marks
    return marks


def nearest_frame(frames: list[dict[str, Any]], target: float) -> dict[str, Any] | None:
    return min(frames, key=lambda item: (abs(safe_float(item.get("time")) - target), str(item.get("path") or ""))) if frames else None


def frame_path(frame: dict[str, Any] | None) -> str:
    return str((frame or {}).get("path") or "")


def keyframe_role(path_value: str) -> str:
    name = Path(path_value).name
    if "_start_" in name:
        return "start"
    if "_middle_" in name:
        return "middle"
    if "_end_near_" in name:
        return "end_near"
    return ""


def keyframe_number(path_value: str) -> int | None:
    match = re.search(r"pyscenedetect_(?:start|middle|end_near)_(\d+)_", Path(path_value).name)
    return int(match.group(1)) if match else None


def aligned_scene_mark_groups(frames: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(frames):
        frame = frames[index]
        role = keyframe_role(frame_path(frame))
        number = keyframe_number(frame_path(frame))
        if role == "start" and number is not None:
            end_match = None
            middle_match = None
            for next_index in range(index + 1, min(len(frames), index + 4)):
                next_frame = frames[next_index]
                next_role = keyframe_role(frame_path(next_frame))
                next_number = keyframe_number(frame_path(next_frame))
                if next_role == "end_near" and next_number == number + 2:
                    end_match = (next_index, next_frame)
                    break
                if next_role == "middle" and next_number == number + 1:
                    middle_match = (next_index, next_frame)
            match = end_match or middle_match
            if match:
                groups.append([frame, match[1]])
                index = match[0] + 1
                continue
        if role == "middle" and number is not None:
            next_frame = frames[index + 1] if index + 1 < len(frames) else None
            if next_frame:
                next_role = keyframe_role(frame_path(next_frame))
                next_number = keyframe_number(frame_path(next_frame))
                if next_role == "end_near" and next_number == number + 1:
                    groups.append([frame, next_frame])
                    index += 2
                    continue
        index += 1
    return groups


def build_inferred_scene_marks(shot: dict[str, Any], frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shot_id = str(shot.get("shot_id") or "")
    groups = aligned_scene_mark_groups(frames)
    if not groups:
        return []
    fallback_text = str((shot.get("reference") or {}).get("srt_text") or shot.get("spoken_script") or "")
    marks: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        first = group[0]
        last = group[-1]
        first_path = frame_path(first)
        last_path = frame_path(last) or first_path
        paths = [frame_path(item) for item in group if frame_path(item)]
        start = safe_float(first.get("time"), safe_float(shot.get("start")))
        end = safe_float(last.get("time"), start)
        marks.append({"scene_mark_id": f"{shot_id}_scene_{index:03d}", "shot_id": shot_id, "scene_index": index, "mode": "first_last" if first_path != last_path else "single", "generation_mode": "first_frame", "start": start, "end": end, "duration": max(0.0, end - start), "keyframes": {"single": first_path if first_path == last_path else "", "first": first_path, "last": last_path, "paths": paths or [p for p in (first_path, last_path) if p]}, "srt_text": fallback_text, "warnings": ["auto_inferred_scene_from_keyframe_triplet"]})
    return marks


def scene_frame_paths(mark: dict[str, Any]) -> tuple[str, str]:
    keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
    single = str(keyframes.get("single") or "").strip()
    first = str(keyframes.get("first") or single).strip()
    last = str(keyframes.get("last") or single or first).strip()
    return first, last


def sync_keyframe_scene_marks(shot: dict[str, Any]) -> None:
    reference = shot.setdefault("reference", {})
    frames = reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else []
    boundaries: dict[str, dict[str, Any]] = {}
    for mark in scene_marks_for_shot(shot):
        scene_mark_id = str(mark.get("scene_mark_id") or "")
        scene_index = mark.get("scene_index")
        first_path, last_path = scene_frame_paths(mark)
        if first_path:
            boundaries[first_path] = {"scene_mark_id": scene_mark_id, "scene_index": scene_index, "role": "first" if first_path != last_path else "single", "click_behavior": "show_scene_description"}
        if last_path and last_path != first_path:
            boundaries[last_path] = {"scene_mark_id": scene_mark_id, "scene_index": scene_index, "role": "last", "click_behavior": "show_scene_description"}
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        marker = boundaries.get(str(frame.get("path") or ""))
        if marker:
            frame["scene_mark"] = marker
        else:
            frame.pop("scene_mark", None)


def generation_mode(mark: dict[str, Any]) -> str:
    value = str(mark.get("generation_mode") or mark.get("asset_generation_mode") or mark.get("mode") or "").strip()
    return "first_last" if value == "first_last" else "first_frame"


def ensure_scene_marks(shot: dict[str, Any], frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shot_id = str(shot.get("shot_id") or "")
    reference = shot.setdefault("reference", {})
    marks = scene_marks_for_shot(shot)
    inferred = build_inferred_scene_marks(shot, frames)
    has_confirmed_marks = any((mark.get("mark_status") if isinstance(mark.get("mark_status"), dict) else {}).get("first_last_confirmed") for mark in marks)
    if inferred and not has_confirmed_marks and (not marks or len(inferred) > len(marks)):
        reference["scene_marks"] = inferred
        reference["scene_mark_summary"] = {**(reference.get("scene_mark_summary") if isinstance(reference.get("scene_mark_summary"), dict) else {}), "tool": TOOL_ID, "tool_version": TOOL_VERSION, "mark_mode": "auto_first_last", "scene_count": len(inferred), "generated_at": now_ms(), "inference_mode": "pyscenedetect_keyframe_triplets", "replaced_scene_count": len(marks)}
        return inferred
    if marks or not frames:
        return marks
    first, last = frames[0], frames[-1]
    first_path = frame_path(first)
    last_path = frame_path(last) or first_path
    mark = {"scene_mark_id": f"{shot_id}_scene_001", "shot_id": shot_id, "scene_index": 1, "mode": "first_last" if first_path != last_path else "single", "generation_mode": "first_frame", "start": safe_float(first.get("time"), safe_float(shot.get("start"))), "end": safe_float(last.get("time"), safe_float(shot.get("end"))), "keyframes": {"single": first_path if first_path == last_path else "", "first": first_path, "last": last_path, "paths": [p for p in (first_path, last_path) if p]}, "srt_text": str((shot.get("reference") or {}).get("srt_text") or shot.get("spoken_script") or ""), "warnings": ["auto_created_single_scene_from_remaining_keyframes"]}
    shot.setdefault("reference", {})["scene_marks"] = [mark]
    return [mark]


def mark_scene(shot: dict[str, Any], mark: dict[str, Any], frames: list[dict[str, Any]], index: int) -> dict[str, Any]:
    shot_id = str(shot.get("shot_id") or "")
    first_path, last_path = scene_frame_paths(mark)
    if not first_path and frames:
        first_path = frame_path(nearest_frame(frames, safe_float(mark.get("start"), safe_float(shot.get("start")))))
    if not last_path and frames:
        last_path = frame_path(nearest_frame(frames, safe_float(mark.get("end"), safe_float(mark.get("start"), safe_float(shot.get("end")))))) or first_path
    keyframes = mark.setdefault("keyframes", {})
    keyframes["first"] = first_path
    keyframes["last"] = last_path
    if generation_mode(mark) != "first_last":
        keyframes["single"] = first_path
    keyframes["paths"] = [p for p in (first_path, last_path) if p]
    mark.setdefault("shot_id", shot_id)
    mark.setdefault("scene_mark_id", f"{shot_id}_scene_{index:03d}")
    mark.setdefault("scene_index", index)
    mark_status = mark.setdefault("mark_status", {})
    mark_status["first_last_marked"] = bool(first_path and last_path)
    mark_status.setdefault("first_last_confirmed", False)
    mark_status["marked_at"] = now_ms()
    mark_status["marked_by"] = TOOL_ID
    return {"shot_id": shot_id, "scene_mark_id": mark.get("scene_mark_id"), "status": "marked" if first_path and last_path else "blocked", "first": first_path, "last": last_path, "warnings": [] if first_path and last_path else ["missing_keyframe_path"]}


def target_shots(plan: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    requested = {str(item) for item in args.shot_id if str(item)}
    shots = shot_list(plan)
    return [shot for shot in shots if not requested or str(shot.get("shot_id") or "") in requested]


def run(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    plan = read_json(workspace / args.input)
    results: list[dict[str, Any]] = []
    for shot in target_shots(plan, args):
        frames = keyframes_for_shot(shot)
        marks = ensure_scene_marks(shot, frames)
        results.extend(mark_scene(shot, mark, frames, index) for index, mark in enumerate(marks, start=1))
        sync_keyframe_scene_marks(shot)
    write_json(workspace / args.output, plan)
    return {"status": "completed_with_blockers" if any(item["status"] == "blocked" for item in results) else "completed", "result_count": len(results), "results": results}


def scope(args: argparse.Namespace) -> dict[str, Any]:
    return {"shot_id": args.shot_id} if args.shot_id else {}


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    satisfied: list[Any] = []
    missing: list[dict[str, Any]] = []
    plan_path = workspace / args.input
    if not plan_path.exists():
        missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"required workspace file does not exist: {args.input}", "suggested_tools": ["02_RebuildShotPlanBuilder"], "scope": scope(args)})
    else:
        satisfied.append("rebuild_shot_plan.json")
        try:
            shots = target_shots(read_json(plan_path), args)
            if any(keyframes_for_shot(shot) for shot in shots):
                satisfied.append("saved_shot_keyframes")
            else:
                missing.append({"dependency": "saved_shot_keyframes", "reason": "no target shots have saved reference.keyframes", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
        except Exception as exc:
            missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"failed to read shot plan: {exc}", "suggested_tools": ["02_RebuildShotPlanBuilder"], "scope": scope(args)})
    return {"status": "blocked" if missing else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": []}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default="OPENCREW_DATABASE_URL")
    parser.add_argument("--shot-id", action="append", default=[])
    parser.add_argument("--scene-mark-id", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    dependencies = check_dependencies(workspace, args)
    try:
        if args.check_dependencies_only or (dependencies["missing"] and not args.force):
            status = "blocked" if dependencies["missing"] else "completed"
            result = None
        else:
            result = run(workspace, args)
            status = result.get("status", "completed")
        payload = {"tool": TOOL_ID, "tool_version": TOOL_VERSION, "status": status, "workspace": str(workspace), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS, "result": result}
    except Exception as exc:
        payload = {"tool": TOOL_ID, "tool_version": TOOL_VERSION, "status": "failed", "workspace": str(workspace), "message": str(exc), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS}
    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] == "blocked":
        raise SystemExit(2)
    if payload["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
