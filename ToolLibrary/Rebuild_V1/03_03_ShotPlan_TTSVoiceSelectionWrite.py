from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


V1_TOOL_ID = "03_03_ShotPlan_TTSVoiceSelectionWrite"
TOOL_NAME = V1_TOOL_ID
TOOL_VERSION = "1.0.0"
DEFAULT_TTS_PROVIDER = "qwen"
DEFAULT_TTS_MODEL = "qwen3-tts-instruct-flash"
DEFAULT_TTS_VOICE = "Cherry"
REQUIRES = ["rebuild_shot_plan.json"]
OPTIONAL_INPUTS = ["tts/tts_voice_recommendations.json"]
PRODUCES = ["rebuild_shot_plan.json", "reports/rebuild_v1/03_03_ShotPlan_TTSVoiceSelectionWrite.json"]
SUGGESTED_PREVIOUS_TOOLS = ["02_Rebuild_ShotPlanBuilder"]
SUGGESTED_NEXT_TOOLS = ["03_04_ShotPlan_PreDeleteReadinessCheck"]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (plan.get("shots") or []) if isinstance(item, dict)]


def load_recommendations(workspace: Path) -> dict[str, dict[str, Any]]:
    path = workspace / "tts" / "tts_voice_recommendations.json"
    if not path.exists():
        return {}
    payload = read_json(path)
    items = payload.get("recommendations") if isinstance(payload, dict) else []
    return {str(item.get("shot_id") or ""): item for item in items if isinstance(item, dict) and str(item.get("shot_id") or "")}


def append_tool_chain(plan: dict[str, Any]) -> None:
    chain = plan.get("tool_chain") if isinstance(plan.get("tool_chain"), list) else []
    chain.append({"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "generated_at": now_ms()})
    plan["tool_chain"] = chain


def selection_from_args(args: argparse.Namespace) -> dict[str, str]:
    return {
        "provider": str(args.tts_provider or DEFAULT_TTS_PROVIDER),
        "model": str(args.tts_model or DEFAULT_TTS_MODEL),
        "voice": str(args.tts_voice or DEFAULT_TTS_VOICE),
    }


def optional_selection_fields(rec: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in ("prompt", "prompt_template", "instructions", "stage", "audio", "fit_audio", "raw_audio", "candidate_id", "score", "label", "match_source", "reason", "tempo", "speed_factor"):
        value = rec.get(key)
        if value not in (None, ""):
            fields[key] = value
    fit_meta = rec.get("fit_meta")
    if isinstance(fit_meta, dict) and fit_meta:
        fields["fit_meta"] = fit_meta
        if "tempo" not in fields and fit_meta.get("tempo") not in (None, ""):
            fields["tempo"] = fit_meta.get("tempo")
    top_candidates = rec.get("top_candidates")
    if isinstance(top_candidates, list) and top_candidates:
        fields["top_candidates"] = top_candidates
    return fields


def write_tts_selection(plan: dict[str, Any], workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    recommendations = load_recommendations(workspace)
    default_selection = selection_from_args(args)
    results: list[dict[str, Any]] = []
    for shot in shot_list(plan):
        shot_id = str(shot.get("shot_id") or "")
        rec = recommendations.get(shot_id) or {}
        source = "tts_voice_recommendations" if rec else "cli_default" if (args.tts_provider or args.tts_model or args.tts_voice) else "tool_default"
        selection = {
            "provider": rec.get("provider") or default_selection["provider"],
            "model": rec.get("model") or default_selection["model"],
            "voice": rec.get("voice") or default_selection["voice"],
            "selection_source": source,
            "selected_at": now_ms(),
            **optional_selection_fields(rec),
        }
        shot["tts_selection"] = selection
        results.append({"shot_id": shot_id, "status": "written", "selection_source": source, "tts_selection": selection})
    plan["tts_selection_summary"] = {**default_selection, "written_at": now_ms()}
    append_tool_chain(plan)
    output_path = workspace / str(args.output)
    write_json(output_path, plan)
    status = {"status": "completed", "shot_count": len(results), "recommendation_count": len(recommendations), "results": results, "output": str(args.output)}
    write_json(workspace / "reports" / "rebuild_v1" / f"{V1_TOOL_ID}.json", status)
    return status


def dependency_file_path(workspace: Path, dependency: str, args: argparse.Namespace) -> Path | None:
    if dependency == "rebuild_shot_plan.json":
        return workspace / str(args.input)
    return None


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    satisfied: list[str] = []
    missing: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for dependency in REQUIRES:
        path = dependency_file_path(workspace, dependency, args)
        if path and path.exists():
            satisfied.append(dependency)
        else:
            missing.append({"dependency": dependency, "reason": f"required workspace file does not exist: {path.relative_to(workspace) if path else dependency}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS})
    for optional in OPTIONAL_INPUTS:
        path = workspace / optional
        if not path.exists():
            warnings.append({"dependency": optional, "reason": "optional TTS recommendation file is absent; tool will use CLI/default TTS selection"})
    return {"status": "blocked" if missing else "warning" if warnings else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": warnings}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {V1_TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="rebuild_shot_plan.json")
    parser.add_argument("--tts-provider", default=DEFAULT_TTS_PROVIDER)
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL)
    parser.add_argument("--tts-voice", default=DEFAULT_TTS_VOICE)
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
            result_payload = {"tool": V1_TOOL_ID, "tool_version": TOOL_VERSION, "status": "blocked" if dependencies["missing"] else "completed_with_warnings" if dependencies["warnings"] else "completed", "workspace": str(workspace), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS, "result": None}
        elif dependencies["missing"] and not args.force:
            result_payload = {"tool": V1_TOOL_ID, "tool_version": TOOL_VERSION, "status": "blocked", "workspace": str(workspace), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS, "result": None}
        else:
            plan = read_json(workspace / str(args.input))
            if not isinstance(plan, dict):
                raise RuntimeError("rebuild_shot_plan.json must contain a JSON object")
            result = write_tts_selection(plan, workspace, args)
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
