from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


TOOL_ID = "05_01_ShotPlan_ScenePromptRefresh"
TOOL_NAME = "ShotPlan Scene Prompt Refresh"
TOOL_VERSION = "1.0.0"
REQUIRES = ["rebuild_shot_plan.json", "source_package.json", "confirmed_first_last", "task_id", "opencode_session_context", "run_model"]
PRODUCES = ["rebuild_shot_plan.json", f"reports/rebuild_v1/{TOOL_ID}.json"]
SUGGESTED_PREVIOUS_TOOLS = ["04_03_ShotPlan_FirstLastReadinessCheck"]
SUGGESTED_NEXT_TOOLS: list[str] = []


def load_scene_tool() -> Any:
    path = Path(__file__).with_name("05_01_Scene_ScenePromptRefresh.py")
    spec = importlib.util.spec_from_file_location("rebuild_v1_05_01_scene", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load scene prompt refresh tool: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scene_tool = load_scene_tool()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def target_shots(plan: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    requested = {str(item) for item in args.shot_id if str(item)}
    return [shot for shot in scene_tool.shot_list(plan) if not requested or str(shot.get("shot_id") or "") in requested]


def scope(args: argparse.Namespace) -> dict[str, Any]:
    return {"shot_id": args.shot_id} if args.shot_id else {}


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    satisfied: list[Any] = []
    missing: list[dict[str, Any]] = []
    if not args.task_id:
        missing.append({"dependency": "task_id", "reason": "05_01 requires --task-id to load OC-Rebuild run model and OpenCode session context", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
    for name, path in (("rebuild_shot_plan.json", workspace / args.input), ("source_package.json", workspace / args.source_package)):
        if path.exists():
            satisfied.append(name)
        else:
            missing.append({"dependency": name, "reason": f"required workspace file does not exist: {path.name}", "suggested_tools": ["01_Rebuild_SourcePackageLoad" if name == "source_package.json" else "02_RebuildShotPlanBuilder"], "scope": scope(args)})
    if not missing:
        plan = scene_tool.read_json(workspace / args.input)
        marks = [mark for shot in target_shots(plan, args) for mark in scene_tool.scene_marks_for_shot(shot)]
        if not marks:
            missing.append({"dependency": "scene_marks", "reason": "target shots have no scene marks", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
        elif all(scene_tool.is_confirmed(mark) for mark in marks):
            satisfied.append("confirmed_first_last")
        else:
            missing.append({"dependency": "confirmed_first_last", "reason": "one or more scenes are missing confirmed first/last frames", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
    if args.task_id:
        satisfied.extend(["task_id", "opencode_session_context", "run_model"])
    return {"status": "blocked" if missing else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": []}


def run(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    plan = scene_tool.read_json(workspace / args.input)
    source_package = scene_tool.read_json(workspace / args.source_package)
    context = scene_tool.fetch_rebuild_context(scene_tool.resolve_database_url(args), int(args.task_id))
    scene_tool.validate_rebuild_context_for_workspace(workspace, plan, context)
    image_workspace = scene_tool.source_workspace_from_package(workspace, source_package) or workspace
    results: list[dict[str, Any]] = []
    for shot in target_shots(plan, args):
        results.extend(scene_tool.refresh_scene(context, source_package, shot, mark, image_workspace, int(args.timeout_seconds)) for mark in scene_tool.scene_marks_for_shot(shot))
    scene_tool.write_json(workspace / args.output, plan)
    report = {"status": "completed_with_blockers" if any(item["status"] != "completed" for item in results) else "completed", "calibration": "image_ocr_srt_aligned", "result_count": len(results), "results": results}
    write_json(workspace / "reports" / "rebuild_v1" / f"{TOOL_ID}.json", report)
    return report


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
    parser.add_argument("--timeout-seconds", type=int, default=300)
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
