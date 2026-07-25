from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


TOOL_ID = "04_02_ShotPlan_FirstLastFrameConfirm"
TOOL_NAME = "ShotPlan First Last Frame Confirm"
TOOL_VERSION = "1.0.0"
REQUIRES = ["rebuild_shot_plan.json", "marked_first_last"]
PRODUCES = ["rebuild_shot_plan.json", f"reports/rebuild_v1/{TOOL_ID}.json"]
SUGGESTED_PREVIOUS_TOOLS = ["04_01_ShotPlan_FirstLastFrameMark"]
SUGGESTED_NEXT_TOOLS = ["04_03_ShotPlan_FirstLastReadinessCheck"]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("shots", []) if isinstance(item, dict)]


def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    return [item for item in reference.get("scene_marks", []) if isinstance(item, dict)]


def target_shots(plan: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    requested = {str(item) for item in args.shot_id if str(item)}
    return [shot for shot in shot_list(plan) if not requested or str(shot.get("shot_id") or "") in requested]


def generation_mode(mark: dict[str, Any]) -> str:
    value = str(mark.get("generation_mode") or mark.get("asset_generation_mode") or mark.get("mode") or "").strip()
    return "first_last" if value == "first_last" else "first_frame"


def scene_frame_paths(mark: dict[str, Any]) -> tuple[str, str]:
    keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
    single = str(keyframes.get("single") or "").strip()
    first = str(keyframes.get("first") or single).strip()
    last = str(keyframes.get("last") or single or first).strip()
    return first, last


def can_confirm(mark: dict[str, Any]) -> tuple[bool, list[str]]:
    first, last = scene_frame_paths(mark)
    if generation_mode(mark) == "first_last":
        return bool(first and last), [] if first and last else ["missing_first_or_last"]
    return bool(first), [] if first else ["missing_first_frame"]


def confirm_scene(shot: dict[str, Any], mark: dict[str, Any]) -> dict[str, Any]:
    confirmed, warnings = can_confirm(mark)
    first, last = scene_frame_paths(mark)
    mark_status = mark.setdefault("mark_status", {})
    mark_status["first_last_marked"] = bool(first and last)
    mark_status["first_last_confirmed"] = confirmed
    mark_status["confirmed_at"] = now_ms()
    mark_status["confirmed_by"] = TOOL_ID
    return {"shot_id": shot.get("shot_id"), "scene_mark_id": mark.get("scene_mark_id"), "status": "confirmed" if confirmed else "blocked", "first": first, "last": last, "warnings": warnings}


def run(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    plan = read_json(workspace / args.input)
    results: list[dict[str, Any]] = []
    for shot in target_shots(plan, args):
        results.extend(confirm_scene(shot, mark) for mark in scene_marks_for_shot(shot))
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
            marks = [mark for shot in shots for mark in scene_marks_for_shot(shot)]
            if not marks:
                missing.append({"dependency": "scene_marks", "reason": "target shots have no scene marks", "suggested_tools": ["04_01_ShotPlan_FirstLastFrameMark"], "scope": scope(args)})
            elif all(can_confirm(mark)[0] for mark in marks):
                satisfied.append("marked_first_last")
            else:
                missing.append({"dependency": "marked_first_last", "reason": "one or more scene marks are missing required frames", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
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
            status, result = ("blocked" if dependencies["missing"] else "completed"), None
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
