from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLLIB_ROOT = REPO_ROOT / "ToolLibrary"
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))

try:
    from OpenCut_V1.core.media_binaries import find_ffmpeg, media_env
    from OpenCut_V1.scene_runtime import (
        BlockedError,
        base_result,
        finish_error,
        load_inputs,
        now_iso,
        read_json,
        relpath,
        remove_path,
        resolve_workspace,
        source_fingerprint,
        write_json,
    )
except Exception:  # pragma: no cover - direct script fallback
    from core.media_binaries import find_ffmpeg, media_env  # type: ignore
    from scene_runtime import (  # type: ignore
        BlockedError,
        base_result,
        finish_error,
        load_inputs,
        now_iso,
        read_json,
        relpath,
        remove_path,
        resolve_workspace,
        source_fingerprint,
        write_json,
    )


TOOL_NAME = "03_02_SceneKeyframeIndex"
TOOL_VERSION = "0.3.0"
TOOL_DIR = "S3_03_02_SceneKeyframeIndex"
INPUT_SCENES_REL = "SessionOutput/visual/scene_detect_scenes.json"
OUTPUT_FINAL_REL = f"{TOOL_DIR}/Output/final_scene_frame_items.json"
STATE_REL = f"{TOOL_DIR}/State.json"
REPORT_REL = f"{TOOL_DIR}/Report/Result.json"
SESSION_FINAL_REL = "SessionOutput/visual/final_scene_frame_items.json"
SESSION_FRAMES_REL = "SessionOutput/visual/scene_frames"
SAMPLING_STRATEGY = "scene_uniform_4_v1"
SAMPLING_RATIOS = (0.125, 0.375, 0.625, 0.875)


@dataclass(frozen=True)
class Args:
    workspace: str
    force: bool
    resume: bool
    print_json: bool


def candidate_times(
    start: float,
    end: float,
    video_duration: float,
    *,
    slot_index: int,
) -> list[float]:
    """Return deterministic retries contained in one quarter of the fragment."""

    if slot_index not in range(4):
        raise ValueError("visual_sample_slot_invalid")
    span = max(0.001, end - start)
    quarter_start = start + span * (slot_index / 4)
    quarter_end = start + span * ((slot_index + 1) / 4)
    target = start + span * SAMPLING_RATIOS[slot_index]
    quarter_span = max(0.001, quarter_end - quarter_start)
    # Retry near the target but never borrow evidence from another slot.
    points = [
        target,
        target - quarter_span * 0.1,
        target + quarter_span * 0.1,
        target - quarter_span * 0.2,
        target + quarter_span * 0.2,
        quarter_start + quarter_span * 0.05,
        quarter_end - quarter_span * 0.05,
    ]
    global_upper = max(0.0, video_duration - 0.001)
    slot_upper = min(global_upper, end - 0.001, quarter_end - 0.001)
    slot_lower = max(0.0, start, quarter_start)
    if slot_upper < slot_lower:
        slot_upper = slot_lower
    return list(
        dict.fromkeys(
            round(min(max(slot_lower, point), slot_upper), 3)
            for point in points
        )
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def extract_frame(source: Path, output: Path, times: list[float]) -> float:
    output.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for time_value in times:
        output.unlink(missing_ok=True)
        command = [
            find_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{time_value:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-an",
            "-q:v",
            "2",
            "-y",
            str(output),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False, env=media_env())
        if completed.returncode == 0 and output.is_file() and output.stat().st_size > 0:
            return time_value
        errors.append((completed.stderr or completed.stdout or f"ffmpeg exited {completed.returncode}").strip())
    raise RuntimeError("; ".join(error for error in errors if error) or f"Failed to extract keyframe: {output.name}")


def build_final_items(
    scenes_payload: dict[str, Any],
    *,
    source: Path,
    workspace: Path,
    video_duration: float,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes_payload.get("scenes") or [], start=1):
        if not isinstance(scene, dict):
            continue
        scene_id = str(scene.get("scene_id") or f"scene_{index:04d}")
        start = max(0.0, float(scene.get("start") or 0.0))
        end = min(video_duration, float(scene.get("end") or start))
        if end <= start:
            raise RuntimeError(f"Invalid Scene range for {scene_id}: {start} - {end}")
        keyframes: list[dict[str, Any]] = []
        for slot_index in range(4):
            sample_number = slot_index + 1
            keyframe_id = f"{scene_id}-sample-{sample_number:02d}"
            frame_rel = f"{SESSION_FRAMES_REL}/{keyframe_id}.jpg"
            frame_path = workspace / frame_rel
            frame_time = extract_frame(
                source,
                frame_path,
                candidate_times(
                    start,
                    end,
                    video_duration,
                    slot_index=slot_index,
                ),
            )
            keyframes.append(
                {
                    "keyframe_id": keyframe_id,
                    "keyframe_time": frame_time,
                    "image_path": frame_rel,
                    "image_sha256": sha256_file(frame_path),
                }
            )
        representative = keyframes[1]
        items.append({
            "scene_id": scene_id,
            "title": f"Scene {index:04d}",
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "start_frame": int(scene.get("start_frame") or 0),
            "end_frame": int(scene.get("end_frame") or 0),
            # These two legacy projection fields keep old result readers safe;
            # the authoritative v2 publisher consumes the ordered array below.
            "keyframe_time": representative["keyframe_time"],
            "image_path": representative["image_path"],
            "sampling_strategy": SAMPLING_STRATEGY,
            "keyframes": keyframes,
            "source_detectors": list(scene.get("source_detectors") or []),
            "segment_kind": str(scene.get("segment_kind") or "detected_scene"),
            "source_scene_index": int(scene.get("source_scene_index") or index),
            "analysis_window_index": int(scene.get("analysis_window_index") or 1),
            "analysis_window_count": int(scene.get("analysis_window_count") or 1),
            "confidence": float(scene.get("confidence") or 0.0),
            "usability": "detected",
        })
    if not items:
        raise RuntimeError("Scene Detect produced no valid scenes.")
    return {
        "schema_version": "opencut_v1_scene_frame_items_0.2",
        "source_video_path": relpath(source, workspace),
        "source_fingerprint": source_fingerprint(source),
        "scene_count": len(items),
        "sampling_strategy": SAMPLING_STRATEGY,
        "keyframe_count": len(items) * 4,
        "items": items,
        "created_at": now_iso(),
    }


def reusable(workspace: Path, fingerprint: str) -> dict[str, Any] | None:
    path = workspace / SESSION_FINAL_REL
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except Exception:
        return None
    if payload.get("source_fingerprint") != fingerprint:
        return None
    if payload.get("sampling_strategy") != SAMPLING_STRATEGY:
        return None
    for item in payload.get("items") or []:
        keyframes = item.get("keyframes") if isinstance(item, dict) else None
        if not isinstance(keyframes, list) or len(keyframes) != 4:
            return None
        scene_id = str(item.get("scene_id") or "")
        for slot_index, frame in enumerate(keyframes, start=1):
            if not isinstance(frame, dict):
                return None
            if frame.get("keyframe_id") != (
                f"{scene_id}-sample-{slot_index:02d}"
            ):
                return None
            raw_path = str(frame.get("image_path") or "")
            if not raw_path or Path(raw_path).is_absolute():
                return None
            unresolved = workspace / raw_path
            resolved = unresolved.resolve()
            if (
                unresolved.is_symlink()
                or not resolved.is_relative_to(workspace.resolve())
                or not resolved.is_file()
                or str(frame.get("image_sha256") or "")
                != sha256_file(resolved)
            ):
                return None
    return payload


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(TOOL_NAME, TOOL_VERSION, workspace, force=args.force, resume=args.resume)
    try:
        if args.force:
            for rel in (TOOL_DIR, SESSION_FINAL_REL, SESSION_FRAMES_REL):
                remove_path(workspace / rel)
                result["cleanup_actions"].append({"path": rel, "action": "removed_for_force_rerun"})
        variables, source, metadata = load_inputs(workspace)
        fingerprint = source_fingerprint(source)
        existing = reusable(workspace, fingerprint) if args.resume and not args.force else None
        if existing is not None:
            final_payload = existing
            result["warnings"].append({"code": "reused_completed_output", "message": "Existing Scene keyframe index was reused."})
        else:
            scenes_path = workspace / INPUT_SCENES_REL
            if not scenes_path.is_file():
                raise BlockedError("scene_detect_result_missing", f"Scene Detect result is missing: {INPUT_SCENES_REL}")
            scenes_payload = read_json(scenes_path)
            duration = float(metadata.get("duration_seconds") or scenes_payload.get("duration_seconds") or 0.0)
            if duration <= 0:
                raise BlockedError("video_duration_missing", "Scene keyframe indexing requires video duration.")
            remove_path(workspace / SESSION_FRAMES_REL)
            final_payload = build_final_items(scenes_payload, source=source, workspace=workspace, video_duration=duration)
            write_json(workspace / OUTPUT_FINAL_REL, final_payload)
            write_json(workspace / SESSION_FINAL_REL, final_payload)
        items = [item for item in final_payload.get("items") or [] if isinstance(item, dict)]
        result["inputs"] = {"variables": variables, "source_video": relpath(source, workspace), "scenes": INPUT_SCENES_REL}
        keyframe_paths = [
            str(frame.get("image_path") or "")
            for item in items
            for frame in (item.get("keyframes") or [])
            if isinstance(frame, dict)
        ]
        result["outputs"] = {
            "final_items": SESSION_FINAL_REL,
            "scene_frames": SESSION_FRAMES_REL,
            "scene_count": len(items),
            "keyframe_count": len(keyframe_paths),
            "sampling_strategy": SAMPLING_STRATEGY,
        }
        result["created_files"] = [
            OUTPUT_FINAL_REL,
            SESSION_FINAL_REL,
            *keyframe_paths,
            STATE_REL,
            REPORT_REL,
        ]
        write_json(
            workspace / STATE_REL,
            {
                "tool": TOOL_NAME,
                "status": "completed",
                "scene_count": len(items),
                "keyframe_count": len(keyframe_paths),
                "sampling_strategy": SAMPLING_STRATEGY,
                "updated_at": now_iso(),
            },
        )
    except Exception as exc:
        finish_error(result, exc)
    write_json(workspace / REPORT_REL, result)
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Extract four ordered keyframes for every OpenCut V1 detected scene.")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(str(ns.workspace or ""), bool(ns.force), bool(ns.resume), bool(ns.print_json))


def main(argv: list[str] | None = None) -> int:
    cli = argv if argv is not None else sys.argv[1:]
    if "--tool-session-root" in cli:
        from ToolLibrary.OpenCut_V1.framework_bridge import maybe_run_framework_bridge
        bridged = maybe_run_framework_bridge(cli, script_path=Path(__file__), tool_name=TOOL_NAME)
        if bridged is not None:
            return bridged
    args = parse_args(cli)
    result = run(args)
    code = 0 if result["status"] == "completed" else 2 if result["status"] == "blocked" else 1
    if args.print_json or code:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
