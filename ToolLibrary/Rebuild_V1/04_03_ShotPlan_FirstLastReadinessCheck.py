from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


TOOL_ID = "04_03_ShotPlan_FirstLastReadinessCheck"
TOOL_NAME = "ShotPlan First Last Readiness Check"
TOOL_VERSION = "1.0.0"
REQUIRES = ["rebuild_shot_plan.json", "confirmed_first_last"]
PRODUCES = [f"reports/rebuild_v1/{TOOL_ID}.json"]
SUGGESTED_PREVIOUS_TOOLS = ["04_02_ShotPlan_FirstLastFrameConfirm", "04_02_Shot_FirstLastFrameConfirm"]
SUGGESTED_NEXT_TOOLS = ["05_01_ShotPlan_ScenePromptRefresh"]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def scene_ready(mark: dict[str, Any]) -> tuple[bool, list[str]]:
    first, last = scene_frame_paths(mark)
    mark_status = mark.get("mark_status") if isinstance(mark.get("mark_status"), dict) else {}
    confirmed = bool(mark_status.get("first_last_confirmed"))
    warnings: list[str] = []
    if generation_mode(mark) == "first_last" and not (first and last):
        warnings.append("missing_first_or_last")
    if generation_mode(mark) != "first_last" and not first:
        warnings.append("missing_first_frame")
    if not confirmed:
        warnings.append("not_confirmed")
    return not warnings, warnings


def build_report(plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    shots_payload: list[dict[str, Any]] = []
    blockers: list[str] = []
    for shot in target_shots(plan, args):
        shot_id = str(shot.get("shot_id") or "")
        marks = scene_marks_for_shot(shot)
        scene_rows: list[dict[str, Any]] = []
        if not marks:
            blockers.append(f"{shot_id}: missing_scene_marks")
        for mark in marks:
            ready, warnings = scene_ready(mark)
            scene_id = str(mark.get("scene_mark_id") or "")
            if not ready:
                blockers.append(f"{shot_id}/{scene_id}: {','.join(warnings)}")
            first, last = scene_frame_paths(mark)
            scene_rows.append({"scene_mark_id": scene_id, "generation_mode": generation_mode(mark), "status": "ready" if ready else "blocked", "first": first, "last": last, "warnings": warnings})
        shots_payload.append({"shot_id": shot_id, "status": "blocked" if any(item["status"] == "blocked" for item in scene_rows) or not marks else "ready", "scene_count": len(marks), "scenes": scene_rows})
    return {"status": "completed_with_blockers" if blockers else "completed", "readiness": "first_last", "blocking_errors": blockers, "shots": shots_payload}


def scope(args: argparse.Namespace) -> dict[str, Any]:
    return {"shot_id": args.shot_id} if args.shot_id else {}


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    missing: list[dict[str, Any]] = []
    satisfied: list[Any] = []
    plan_path = workspace / args.input
    if not plan_path.exists():
        missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"required workspace file does not exist: {args.input}", "suggested_tools": ["02_RebuildShotPlanBuilder"], "scope": scope(args)})
    else:
        satisfied.append("rebuild_shot_plan.json")
        try:
            report = build_report(read_json(plan_path), args)
            if report["blocking_errors"]:
                missing.append({"dependency": "confirmed_first_last", "reason": "one or more target scenes are not first/last ready", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
            else:
                satisfied.append("confirmed_first_last")
        except Exception as exc:
            missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"failed to read shot plan: {exc}", "suggested_tools": ["02_RebuildShotPlanBuilder"], "scope": scope(args)})
    return {"status": "blocked" if missing else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": []}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="")
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
        if args.check_dependencies_only and dependencies["missing"]:
            status, result = "blocked", None
        else:
            result = build_report(read_json(workspace / args.input), args) if not dependencies["missing"] or args.force else None
            status = "blocked" if dependencies["missing"] and not args.force else (result or {}).get("status", "completed")
            if result is not None:
                write_json(workspace / "reports" / "rebuild_v1" / f"{TOOL_ID}.json", result)
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
