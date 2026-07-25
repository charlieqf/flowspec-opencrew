from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


TOOL_ID = "03_01_ShotPlan_TTSReferenceAudioExtract"
TOOL_NAME = TOOL_ID
TOOL_VERSION = "1.0.0"
REQUIRES = ["source_package.json"]
PRODUCES = ["tts/tts_reference_audio_manifest.json", f"reports/rebuild_v1/{TOOL_ID}.json"]
SUGGESTED_PREVIOUS_TOOLS = ["01_Rebuild_SourcePackageLoad"]
SUGGESTED_NEXT_TOOLS = ["03_02_ShotPlan_TTSVoiceRecommend", "03_03_ShotPlan_TTSVoiceSelectionWrite"]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def source_media_candidates(source_package: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("source_video", "source_audio", "media_path", "video_path", "audio_path"):
        value = source_package.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append({"source": key, "path": value.strip()})
    media = source_package.get("media") if isinstance(source_package.get("media"), dict) else {}
    for key in ("source_video", "source_audio", "path", "video_path", "audio_path"):
        value = media.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append({"source": f"media.{key}", "path": value.strip()})
    video = source_package.get("video") if isinstance(source_package.get("video"), dict) else {}
    value = video.get("path")
    if isinstance(value, str) and value.strip():
        candidates.append({"source": "video.path", "path": value.strip()})
    metadata = video.get("metadata") if isinstance(video.get("metadata"), dict) else {}
    value = metadata.get("workspace_source_video_path")
    if isinstance(value, str) and value.strip():
        candidates.append({"source": "video.metadata.workspace_source_video_path", "path": value.strip()})
    return candidates


def analysis_workspace(source_package: dict[str, Any]) -> Path | None:
    source = source_package.get("source") if isinstance(source_package.get("source"), dict) else {}
    value = source.get("analysis_workspace")
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser()
    return None


def reference_audio_candidates(source_package: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    workspace = analysis_workspace(source_package)
    if workspace:
        for relative in (
            "audio/reference_audio.wav",
            "outbox/reference_audio.wav",
            "audio/asr_enhanced_audio.wav",
            "audio/original_audio.wav",
        ):
            path = workspace / relative
            candidates.append({
                "source": f"source.analysis_workspace/{relative}",
                "path": str(path),
                "exists": path.exists(),
            })
    for item in source_media_candidates(source_package):
        path = Path(str(item.get("path") or "")).expanduser()
        if path.suffix.lower() in {".wav", ".mp3", ".m4a", ".aac"}:
            candidates.append({**item, "exists": path.exists()})
    return candidates


def select_reference_audio(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in candidates:
        path = Path(str(item.get("path") or "")).expanduser()
        if item.get("exists") and path.is_file():
            return {
                "path": str(path.resolve()),
                "source": item.get("source") or "",
                "format": path.suffix.lower().lstrip("."),
                "size_bytes": path.stat().st_size,
            }
    return None


def run(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    source_package = read_json(workspace / args.source_package)
    media_candidates = source_media_candidates(source_package)
    audio_candidates = reference_audio_candidates(source_package)
    reference_audio = select_reference_audio(audio_candidates)
    manifest = {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "generated_at": now_ms(),
        "status": "completed" if reference_audio else "completed_with_warnings",
        "reference_audio": reference_audio,
        "media_candidates": media_candidates,
        "audio_candidates": audio_candidates,
        "warnings": [] if reference_audio else ["no reference audio found in source_package analysis workspace"],
    }
    write_json(workspace / "tts" / "tts_reference_audio_manifest.json", manifest)
    write_json(workspace / "reports" / "rebuild_v1" / f"{TOOL_ID}.json", manifest)
    return manifest


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    source_path = workspace / args.source_package
    if source_path.exists():
        return {"status": "satisfied", "satisfied": ["source_package.json"], "missing": [], "warnings": []}
    return {
        "status": "blocked",
        "satisfied": [],
        "missing": [{"dependency": "source_package.json", "reason": f"required workspace file does not exist: {args.source_package}", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS}],
        "warnings": [],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="tts/tts_reference_audio_manifest.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default="OPENCREW_DATABASE_URL")
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
