from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

TOOL_ID = "08_03_ShotPlan_PlanA_ImageSequenceCompose"
TOOL_NAME = "08_03_ShotPlan_PlanA_ImageSequenceCompose"
TOOL_VERSION = "1.0.0"
TOOL_LEVEL = "shotplan"
CHILD_TOOL_ID = "08_03_Shot_PlanA_ImageSequenceCompose"
CHILD_SCRIPT = Path(__file__).with_name(f"{CHILD_TOOL_ID}.py")
REQUIRES = ["rebuild_shot_plan.json"]
PRODUCES = ["reports/plan_a/08_03_ShotPlan_PlanA_ImageSequenceCompose.json"]
SUGGESTED_PREVIOUS_TOOLS = ['08_02_ShotPlan_PlanA_HyperframeSubtitleAlign']
SUGGESTED_NEXT_TOOLS = ['09_01_ShotPlan_PlanA_AssemblyBuild']

class ToolError(RuntimeError):
    pass

def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))

def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def now_ms() -> int:
    return int(time.time() * 1000)

def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("shots", []) if isinstance(item, dict)]

def shot_id_of(shot: dict[str, Any]) -> str:
    return str(shot.get("shot_id") or shot.get("id") or "")

def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    ref = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    return [item for item in ref.get("scene_marks", []) if isinstance(item, dict)]

def scene_id_of(mark: dict[str, Any]) -> str:
    return str(mark.get("scene_mark_id") or mark.get("id") or "")

def target_shots(plan: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    wanted = {str(item) for item in getattr(args, "shot_id", []) if str(item)}
    shots = [shot for shot in shot_list(plan) if not wanted or shot_id_of(shot) in wanted]
    if wanted and not shots:
        raise ToolError(f"No shots matched --shot-id: {sorted(wanted)}")
    return shots

def target_scene_marks(shot: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    scene_mark_id = str(getattr(args, "scene_mark_id", "") or "")
    marks = scene_marks_for_shot(shot)
    if scene_mark_id:
        marks = [mark for mark in marks if scene_id_of(mark) == scene_mark_id]
        if not marks:
            raise ToolError(f"Scene mark not found: {scene_mark_id}")
    return marks

def rel(workspace: Path, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(workspace.resolve()))
    except Exception:
        return str(path)

def scope_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {"shot_id": getattr(args, "shot_id", []), "scene_mark_id": getattr(args, "scene_mark_id", "")}

def load_plan(workspace: Path, input_name: str) -> dict[str, Any]:
    path = workspace / input_name
    if not path.exists():
        raise ToolError(f"missing shot plan: {path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ToolError(f"shot plan must be an object: {path}")
    return payload

def base_child_args(args: argparse.Namespace) -> list[str]:
    child = [
        sys.executable,
        str(CHILD_SCRIPT),
        "--workspace", str(args.workspace),
        "--task-id", str(args.task_id),
        "--session-id", str(args.session_id),
        "--input", args.input,
        "--output", args.output,
        "--source-package", args.source_package,
        "--tts-provider", args.tts_provider,
        "--tts-model", args.tts_model,
        "--tts-voice", args.tts_voice,
        "--database-url", args.database_url,
        "--database-url-env", args.database_url_env,
        "--print-json",
    ]
    if args.force:
        child.append("--force")
    if args.force_tts_refresh:
        child.append("--force-tts-refresh")
    if args.check_dependencies_only:
        child.append("--check-dependencies-only")
    return child

def run_child(args: argparse.Namespace, shot_id: str, scene_mark_id: str = "") -> dict[str, Any]:
    command = base_child_args(args) + ["--shot-id", shot_id]
    if scene_mark_id:
        command += ["--scene-mark-id", scene_mark_id]
    result = subprocess.run(command, text=True, capture_output=True)
    payload: dict[str, Any]
    try:
        payload = json.loads(result.stdout or "{}")
    except Exception:
        payload = {"tool": CHILD_TOOL_ID, "status": "failed", "message": "child did not return valid JSON", "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:]}
    payload["exit_code"] = result.returncode
    if result.stderr:
        payload["stderr"] = result.stderr[-2000:]
    return payload

def child_status(payload: dict[str, Any]) -> str:
    return str(payload.get("status") or "failed")

def aggregate_status(results: list[dict[str, Any]]) -> str:
    statuses = [child_status(item.get("child_result") if isinstance(item.get("child_result"), dict) else item) for item in results]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "blocked" for status in statuses):
        return "blocked"
    if any(status == "completed_with_blockers" for status in statuses):
        return "completed_with_blockers"
    if any(status == "completed_with_warnings" for status in statuses):
        return "completed_with_warnings"
    return "completed"

def blocking_errors(results: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for item in results:
        payload = item.get("child_result") if isinstance(item.get("child_result"), dict) else item
        for dep in ((payload.get("dependencies") or {}).get("missing") or []):
            if isinstance(dep, dict):
                errors.append(str(dep.get("reason") or dep.get("dependency") or dep))
            else:
                errors.append(str(dep))
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        for err in result.get("blocking_errors") or []:
            errors.append(str(err))
        if payload.get("message"):
            errors.append(str(payload.get("message")))
    return errors

def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    missing: list[dict[str, Any]] = []
    satisfied: list[Any] = []
    plan_path = workspace / args.input
    if not plan_path.exists():
        missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"required workspace file does not exist: {args.input}", "suggested_tools": ["02_Rebuild_ShotPlanBuilder"], "scope": scope_payload(args)})
    else:
        satisfied.append("rebuild_shot_plan.json")
        try:
            plan = load_plan(workspace, args.input)
            if TOOL_LEVEL == "shot" and len(args.shot_id or []) != 1:
                missing.append({"dependency": "shot_id", "reason": "Shot-level wrapper requires exactly one --shot-id", "suggested_tools": [], "scope": scope_payload(args)})
            elif TOOL_LEVEL == "shot" and not target_scene_marks(target_shots(plan, args)[0], args):
                missing.append({"dependency": "scene_marks", "reason": "target shot has no scene marks", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
            elif TOOL_LEVEL == "shotplan" and not target_shots(plan, args):
                missing.append({"dependency": "shots", "reason": "shot plan has no target shots", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
        except Exception as exc:
            missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"failed to inspect shot plan: {exc}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
    return {"status": "blocked" if missing else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": []}

def run_tool(workspace: Path, plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if TOOL_LEVEL == "shotplan":
        for shot in target_shots(plan, args):
            shot_id = shot_id_of(shot)
            rows.append({"shot_id": shot_id, "child_tool_id": CHILD_TOOL_ID, "child_result": run_child(args, shot_id)})
    elif TOOL_LEVEL == "shot":
        shots = target_shots(plan, args)
        if len(shots) != 1:
            raise ToolError("Shot-level wrapper requires exactly one target shot")
        shot_id = shot_id_of(shots[0])
        for mark in target_scene_marks(shots[0], args):
            scene_id = scene_id_of(mark)
            rows.append({"shot_id": shot_id, "scene_mark_id": scene_id, "child_tool_id": CHILD_TOOL_ID, "child_result": run_child(args, shot_id, scene_id)})
    else:
        raise ToolError(f"unsupported wrapper level: {TOOL_LEVEL}")
    status = aggregate_status(rows)
    payload = {"status": status, "tool_id": TOOL_ID, "child_tool_id": CHILD_TOOL_ID, "level": TOOL_LEVEL, "result_count": len(rows), "blocking_errors": blocking_errors(rows), "results": rows, "generated_at": now_ms()}
    write_json(workspace / "reports" / "plan_a" / f"{TOOL_ID}.json", payload)
    return payload

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 wrapper tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--shot-id", action="append", default=[])
    parser.add_argument("--scene-mark-id", default="")
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--tts-provider", default="qwen")
    parser.add_argument("--tts-model", default="qwen3-tts-instruct-flash")
    parser.add_argument("--tts-voice", default="Cherry")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default="OPENCREW_DATABASE_URL")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-tts-refresh", action="store_true")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    args.workspace = args.workspace.expanduser().resolve()
    dependencies = {"status": "unknown", "satisfied": [], "missing": [], "warnings": []}
    try:
        dependencies = check_dependencies(args.workspace, args)
        if dependencies["missing"] and not args.force:
            result = None
            status = "blocked"
        else:
            plan = load_plan(args.workspace, args.input)
            result = run_tool(args.workspace, plan, args)
            status = str(result.get("status") or "completed")
        payload = {"tool": TOOL_ID, "tool_version": TOOL_VERSION, "status": status, "workspace": str(args.workspace), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS, "result": result}
    except Exception as exc:
        payload = {"tool": TOOL_ID, "tool_version": TOOL_VERSION, "status": "failed", "workspace": str(args.workspace), "message": str(exc), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS}
    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] == "blocked":
        raise SystemExit(2)
    if payload["status"] == "failed":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
