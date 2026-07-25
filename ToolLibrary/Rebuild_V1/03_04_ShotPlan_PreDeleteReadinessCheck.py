from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any


V1_TOOL_ID = "03_04_ShotPlan_PreDeleteReadinessCheck"
TOOL_NAME = V1_TOOL_ID
TOOL_VERSION = "1.0.0"
REQUIRES = ["rebuild_shot_plan.json"]
PRODUCES = ["reports/rebuild_v1/03_04_ShotPlan_PreDeleteReadinessCheck.json", "reports/rebuild_v1_pre_delete_readiness.json"]
SUGGESTED_PREVIOUS_TOOLS = ["03_03_ShotPlan_TTSVoiceSelectionWrite"]
SUGGESTED_NEXT_TOOLS: list[str] = []


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (plan.get("shots") or []) if isinstance(item, dict)]


def strip_srt_timing(text: str) -> str:
    lines: list[str] = []
    for line in str(text or "").splitlines():
        item = line.strip()
        if not item or item.isdigit() or "-->" in item:
            continue
        lines.append(item)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def field_text(value: Any, keys: tuple[str, ...]) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def spoken_script(shot: dict[str, Any]) -> str:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    candidates = [
        reference.get("srt_text"),
        reference.get("spoken_script"),
        shot.get("spoken_script"),
        field_text(shot.get("rebuild_direction"), ("new_spoken_script", "spoken_script", "direction")),
        field_text(shot.get("generation_hint"), ("spoken_script", "voiceover", "hint")),
    ]
    for value in candidates:
        text = strip_srt_timing(str(value or ""))
        if text:
            return text
    return ""


def keyframes_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    keyframes = reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else []
    return [item for item in keyframes if isinstance(item, dict) and str(item.get("path") or "").strip()]


def build_pre_delete_readiness(workspace: Path, plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    shots: list[dict[str, Any]] = []
    blockers: list[str] = []
    all_shots = shot_list(plan)
    if not all_shots:
        blockers.append("shot_plan: missing_shots")
    for shot in all_shots:
        shot_id = str(shot.get("shot_id") or "")
        keyframe_count = len(keyframes_for_shot(shot))
        selection = shot.get("tts_selection") if isinstance(shot.get("tts_selection"), dict) else {}
        spoken_text = spoken_script(shot)
        shot_blockers: list[str] = []
        if keyframe_count == 0:
            shot_blockers.append("missing_keyframes")
        if not (selection.get("provider") and selection.get("model") and selection.get("voice")):
            shot_blockers.append("missing_tts_selection")
        if not spoken_text:
            shot_blockers.append("missing_spoken_text")
        blockers.extend(f"{shot_id}: {item}" for item in shot_blockers)
        shots.append({
            "shot_id": shot_id,
            "keyframe_count": keyframe_count,
            "tts_selection": selection,
            "spoken_text_available": bool(spoken_text),
            "spoken_text_chars": len(spoken_text),
            "status": "blocked" if shot_blockers else "ready",
            "blockers": shot_blockers,
        })
    payload = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed_with_blockers" if blockers else "completed",
        "generated_at": now_ms(),
        "task": {"task_id": args.task_id, "session_id": args.session_id, "workspace": str(workspace)},
        "readiness": "pre_delete",
        "checks": ["shots", "keyframes", "tts_selection", "spoken_text"],
        "not_checked": ["scene_marks", "first_last_confirmed", "scene_prompt", "locked_tts", "image_assets", "renders"],
        "shots": shots,
        "blocking_errors": blockers,
    }
    write_json(workspace / "reports" / "rebuild_v1_pre_delete_readiness.json", payload)
    write_json(workspace / "reports" / "rebuild_v1" / f"{V1_TOOL_ID}.json", payload)
    return payload


def dependency_file_path(workspace: Path, dependency: str, args: argparse.Namespace) -> Path | None:
    if dependency == "rebuild_shot_plan.json":
        return workspace / str(args.input)
    return None


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    satisfied: list[str] = []
    missing: list[dict[str, Any]] = []
    for dependency in REQUIRES:
        path = dependency_file_path(workspace, dependency, args)
        if path and path.exists():
            satisfied.append(dependency)
        else:
            missing.append({"dependency": dependency, "reason": f"required workspace file does not exist: {path.relative_to(workspace) if path else dependency}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS})
    return {"status": "blocked" if missing else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": []}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {V1_TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    dependencies = check_dependencies(workspace, args)
    try:
        if args.check_dependencies_only:
            result_payload = {"tool": V1_TOOL_ID, "tool_version": TOOL_VERSION, "status": "blocked" if dependencies["missing"] else "completed", "workspace": str(workspace), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS, "result": None}
        elif dependencies["missing"] and not args.force:
            result_payload = {"tool": V1_TOOL_ID, "tool_version": TOOL_VERSION, "status": "blocked", "workspace": str(workspace), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS, "result": None}
        else:
            plan = read_json(workspace / str(args.input))
            if not isinstance(plan, dict):
                raise RuntimeError("rebuild_shot_plan.json must contain a JSON object")
            result = build_pre_delete_readiness(workspace, plan, args)
            result_payload = {"tool": V1_TOOL_ID, "tool_version": TOOL_VERSION, "status": result.get("status", "completed"), "workspace": str(workspace), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS, "result": result}
    except Exception as exc:
        result_payload = {"tool": V1_TOOL_ID, "tool_version": TOOL_VERSION, "status": "failed", "workspace": str(workspace), "message": str(exc), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS}
    if args.print_json:
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
    if result_payload.get("status") == "blocked":
        raise SystemExit(2)
    if result_payload.get("status") == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
