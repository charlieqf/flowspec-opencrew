from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

TOOL_ID = "07_01_Shot_PlanA_TTSPromptBuild"
TOOL_NAME = "07_01_Shot_PlanA_TTSPromptBuild"
TOOL_VERSION = "1.0.0"
TOOL_LEVEL = "shot"
REQUIRES = ['rebuild_shot_plan.json', 'scene_srt', 'tts_selection']
PRODUCES = ["reports/plan_a/07_01_Shot_PlanA_TTSPromptBuild.json"]
SUGGESTED_PREVIOUS_TOOLS = ['05_01_Shot_ScenePromptRefresh']
SUGGESTED_NEXT_TOOLS = ['07_02_Shot_PlanA_TTSGenerateAndLock']
DEFAULT_TTS_PROVIDER = "qwen"
DEFAULT_TTS_MODEL = "qwen3-tts-instruct-flash"
DEFAULT_TTS_VOICE = "Cherry"

class ToolError(RuntimeError):
    pass

def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))

def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

def now_ms() -> int:
    return int(time.time() * 1000)

def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "")) or "item"

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def rel(workspace: Path, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(workspace.resolve()))
    except Exception:
        return str(path)

def resolve_workspace_path(workspace: Path, path_value: str) -> Path:
    path = Path(str(path_value or "")).expanduser()
    return path if path.is_absolute() else workspace / path

def load_plan(workspace: Path, input_name: str) -> dict[str, Any]:
    path = workspace / input_name
    if not path.exists():
        raise ToolError(f"missing shot plan: {path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ToolError(f"shot plan must be an object: {path}")
    return payload

def load_source_package(workspace: Path, source_name: str) -> dict[str, Any]:
    for path in (workspace / source_name, workspace / "rebuild" / source_name):
        if path.exists():
            payload = read_json(path)
            return payload if isinstance(payload, dict) else {}
    return {}

def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("shots", []) if isinstance(item, dict)]

def shot_id_of(shot: dict[str, Any]) -> str:
    return str(shot.get("shot_id") or shot.get("id") or "")

def scene_id_of(mark: dict[str, Any]) -> str:
    return str(mark.get("scene_mark_id") or mark.get("id") or "")

def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    ref = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    return [item for item in ref.get("scene_marks", []) if isinstance(item, dict)]

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

def scene_frame_paths(mark: dict[str, Any]) -> tuple[str, str]:
    keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
    single = str(keyframes.get("single") or "").strip()
    first = str(keyframes.get("first") or single).strip()
    last = str(keyframes.get("last") or single or first).strip()
    return first, last

def strip_srt_timing(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return " ".join(lines).strip()

def spoken_script(shot: dict[str, Any]) -> str:
    for key in ("spoken_script", "script", "srt_text"):
        value = str(shot.get(key) or "").strip()
        if value:
            return strip_srt_timing(value)
    return ""

def scene_text(mark: dict[str, Any], fallback: str = "") -> str:
    for key in ("srt_text", "scene_srt", "text", "subtitle"):
        value = str(mark.get(key) or "").strip()
        if value:
            return value
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    for key in ("srt_text", "summary", "video_prompt", "prompt"):
        value = str(desc.get(key) or "").strip()
        if value:
            return value
    return str(fallback or "").strip()

def shot_tts_source(shot: dict[str, Any]) -> dict[str, Any]:
    scene_items = []
    for mark in scene_marks_for_shot(shot):
        text = scene_text(mark, "")
        if text:
            scene_items.append({"scene_mark_id": scene_id_of(mark), "text": text})
    text_value = " ".join(item["text"] for item in scene_items).strip() or spoken_script(shot)
    return {"text": strip_srt_timing(text_value), "source": "scene_srt" if scene_items else "spoken_script", "scene_items": scene_items}

def variant_scene_dir(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return workspace / "Assets" / variant_id / safe_name(shot_id) / safe_name(scene_mark_id)

def canonical_scene_image_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "first.png"

def scene_asset_manifest_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "asset_manifest.json"

def variant_shot_dir(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return workspace / "Assets" / variant_id / safe_name(shot_id)

def shot_tts_dir(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / "tts"

def canonical_locked_tts_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "locked.wav"

def canonical_shot_srt_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "shot.srt"

def canonical_shot_tts_timeline_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "shot_tts_timeline.locked.json"

def plan_a_shot_video_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / "plan_a.mp4"

def source_workspace_from_package(workspace: Path, source_package: dict[str, Any]) -> Path | None:
    for key in ("analysis_workspace", "workspace", "source_workspace"):
        value = str(source_package.get(key) or "").strip()
        if value:
            path = Path(value).expanduser()
            return path if path.is_absolute() else workspace / path
    return None

def resolve_existing_path(workspace: Path, source_package: dict[str, Any], path_value: str) -> Path | None:
    if not path_value:
        return None
    candidates = [resolve_workspace_path(workspace, path_value)]
    source_workspace = source_workspace_from_package(workspace, source_package)
    if source_workspace:
        candidates.append(resolve_workspace_path(source_workspace, path_value))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None

def srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def parse_srt_entries(text: str) -> list[dict[str, Any]]:
    entries = []
    for block in str(text or "").replace("\r\n", "\n").split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing = next((line for line in lines if "-->" in line), "")
        if not timing:
            continue
        body = " ".join(line for line in lines if line != timing and not line.isdigit()).strip()
        entries.append({"timing": timing, "text": body})
    return entries

def ensure_shot_srt_text(shot: dict[str, Any], duration: float) -> str:
    text = str(shot.get("srt_text") or shot.get("shot_srt") or "").strip()
    if text and "-->" in text:
        return text
    clean = strip_srt_timing(text or shot_tts_source(shot).get("text") or spoken_script(shot))
    return f"1\n00:00:00,000 --> {srt_timestamp(max(0.1, duration))}\n{clean}\n"

def scene_image_items(workspace: Path, shot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    shot_id = shot_id_of(shot)
    for mark in scene_marks_for_shot(shot):
        scene_id = scene_id_of(mark)
        image = canonical_scene_image_path(workspace, shot_id, scene_id)
        if image.exists():
            rows.append({"shot_id": shot_id, "scene_mark_id": scene_id, "image": rel(workspace, image), "text": scene_text(mark, spoken_script(shot))})
    return rows

def build_shot_tts_timeline(workspace: Path, shot: dict[str, Any], audio_duration: float, srt_text: str) -> dict[str, Any]:
    shot_id = shot_id_of(shot)
    images = scene_image_items(workspace, shot)
    duration = max(0.1, safe_float(audio_duration, safe_float(shot.get("duration"), 1.0)))
    page_duration = duration / max(1, len(images))
    pages = []
    for index, item in enumerate(images):
        start = round(index * page_duration, 3)
        end = round(duration if index == len(images) - 1 else (index + 1) * page_duration, 3)
        pages.append({**item, "start": start, "end": end, "duration": round(max(0.1, end - start), 3)})
    return {"shot_id": shot_id, "duration": duration, "audio": rel(workspace, canonical_locked_tts_path(workspace, shot_id)), "shot_srt": rel(workspace, canonical_shot_srt_path(workspace, shot_id)), "srt_entries": parse_srt_entries(srt_text), "image_pages": pages, "generated_at": now_ms()}

def scope_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {"shot_id": getattr(args, "shot_id", []), "scene_mark_id": getattr(args, "scene_mark_id", "")}

def enforce_scope(args: argparse.Namespace) -> list[dict[str, Any]]:
    missing = []
    shot_count = len(getattr(args, "shot_id", []) or [])
    scene_id = str(getattr(args, "scene_mark_id", "") or "")
    if TOOL_LEVEL in {"shot", "scene"} and shot_count != 1:
        missing.append({"dependency": "shot_id", "reason": f"{TOOL_LEVEL}-level tool requires exactly one --shot-id", "suggested_tools": [], "scope": scope_payload(args)})
    if TOOL_LEVEL == "scene" and not scene_id:
        missing.append({"dependency": "scene_mark_id", "reason": "scene-level tool requires --scene-mark-id", "suggested_tools": [], "scope": scope_payload(args)})
    return missing

def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    missing = enforce_scope(args)
    satisfied = []
    warnings = []
    plan_path = workspace / args.input
    plan: dict[str, Any] = {}
    if not plan_path.exists():
        missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"required workspace file does not exist: {args.input}", "suggested_tools": ["02_Rebuild_ShotPlanBuilder"], "scope": scope_payload(args)})
    else:
        satisfied.append("rebuild_shot_plan.json")
        try:
            plan = read_json(plan_path)
        except Exception as exc:
            missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"failed to read shot plan: {exc}", "suggested_tools": ["02_Rebuild_ShotPlanBuilder"], "scope": scope_payload(args)})
    if plan:
        try:
            shots = target_shots(plan, args)
        except Exception as exc:
            missing.append({"dependency": "target_scope", "reason": str(exc), "suggested_tools": [], "scope": scope_payload(args)})
            shots = []
        if "source_package.json" in REQUIRES:
            if (workspace / args.source_package).exists() or (workspace / "rebuild" / args.source_package).exists():
                satisfied.append("source_package.json")
            else:
                warnings.append({"dependency": "source_package.json", "reason": "source package not found; only workspace-local paths can be resolved", "scope": scope_payload(args)})
        if "confirmed_first_last" in REQUIRES:
            unconfirmed = []
            for shot in shots:
                for mark in target_scene_marks(shot, args):
                    status = mark.get("mark_status") if isinstance(mark.get("mark_status"), dict) else {}
                    if not status.get("first_last_confirmed"):
                        unconfirmed.append(f"{shot_id_of(shot)}/{scene_id_of(mark)}")
            if unconfirmed:
                missing.append({"dependency": "confirmed_first_last", "reason": f"unconfirmed scene marks: {', '.join(unconfirmed[:10])}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
            else:
                satisfied.append("confirmed_first_last")
        if "scene_first_images" in REQUIRES or "scene_assets" in REQUIRES:
            absent = []
            for shot in shots:
                for mark in target_scene_marks(shot, args):
                    if not canonical_scene_image_path(workspace, shot_id_of(shot), scene_id_of(mark)).exists():
                        absent.append(f"{shot_id_of(shot)}/{scene_id_of(mark)}")
            dep = "scene_first_images" if "scene_first_images" in REQUIRES else "scene_assets"
            if absent:
                missing.append({"dependency": dep, "reason": f"missing scene first images: {', '.join(absent[:10])}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
            else:
                satisfied.append(dep)
        if "scene_srt" in REQUIRES or "tts_text" in REQUIRES:
            absent = [shot_id_of(shot) for shot in shots if not shot_tts_source(shot).get("text")]
            dep = "scene_srt" if "scene_srt" in REQUIRES else "tts_text"
            if absent:
                missing.append({"dependency": dep, "reason": f"missing TTS text for shots: {', '.join(absent[:10])}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
            else:
                satisfied.append(dep)
        if "tts_selection" in REQUIRES:
            satisfied.append("tts_selection")
        for dep, fn in (("locked_tts", canonical_locked_tts_path), ("shot_srt", canonical_shot_srt_path), ("shot_tts_timeline", canonical_shot_tts_timeline_path)):
            if dep in REQUIRES:
                absent = [shot_id_of(shot) for shot in shots if not fn(workspace, shot_id_of(shot)).exists()]
                if absent:
                    missing.append({"dependency": dep, "reason": f"missing {dep} for shots: {', '.join(absent[:10])}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
                else:
                    satisfied.append(dep)
        if "plan_a_videos" in REQUIRES:
            absent = [shot_id_of(shot) for shot in shots if not plan_a_shot_video_path(workspace, shot_id_of(shot)).exists()]
            if absent:
                missing.append({"dependency": "plan_a_videos", "reason": f"missing shot videos: {', '.join(absent[:10])}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope_payload(args)})
            else:
                satisfied.append("plan_a_videos")
    return {"status": "blocked" if missing else "warning" if warnings else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": warnings}

def tts_prompt_build(workspace: Path, plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    items, blockers = [], []
    for shot in target_shots(plan, args):
        shot_id = shot_id_of(shot); source = shot_tts_source(shot); selection = shot.get("tts_selection") if isinstance(shot.get("tts_selection"), dict) else {}; text_value = str(source.get("text") or "").strip(); reason = "" if text_value else "missing_scene_srt_or_spoken_script"
        if reason: blockers.append(f"{shot_id}: {reason}")
        items.append({"shot_id": shot_id, "status": "ready" if not reason else "blocked", "provider": selection.get("provider") or args.tts_provider, "model": selection.get("model") or args.tts_model, "voice": selection.get("voice") or args.tts_voice, "text": text_value, "text_source": source.get("source") or "", "blocking_reason": reason})
    path = variant_shot_dir(workspace, shot_id) / "reports" / "plan_a_07_01_tts_prompt_build.json"; payload = {"status": "completed_with_blockers" if blockers else "completed", "tool_id": TOOL_ID, "blocking_errors": blockers, "item_count": len(items), "items": items}; write_json(path, payload); return {**payload, "output": rel(workspace, path)}

def run_tool(workspace: Path, plan: dict[str, Any], source_package: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    return tts_prompt_build(workspace, plan, args)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--shot-id", action="append", default=[])
    parser.add_argument("--scene-mark-id", default="")
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--tts-provider", default=DEFAULT_TTS_PROVIDER)
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL)
    parser.add_argument("--tts-voice", default=DEFAULT_TTS_VOICE)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default="OPENCREW_DATABASE_URL")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-tts-refresh", action="store_true")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    dependencies = {"status": "unknown", "satisfied": [], "missing": [], "warnings": []}
    try:
        dependencies = check_dependencies(workspace, args)
        if args.check_dependencies_only:
            result = None
            status = "blocked" if dependencies["missing"] else "completed_with_warnings" if dependencies["warnings"] else "completed"
        elif dependencies["missing"] and not args.force:
            result = None
            status = "blocked"
        else:
            plan = load_plan(workspace, args.input)
            source_package = load_source_package(workspace, args.source_package)
            result = run_tool(workspace, plan, source_package, args)
            status = str(result.get("status") or "completed")
            write_json(workspace / "reports" / "plan_a" / f"{TOOL_ID}.json", result)
            if args.output:
                write_json(workspace / args.output, plan)
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
