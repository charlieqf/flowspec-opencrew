from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_NAME = "RebuildAssetTaskBuilder"
TOOL_VERSION = "0.1.0"

DEFAULT_VARIANT_ID = "variant_001"
DEFAULT_MODE = "keyframe_pair_i2v_chunked_with_hyperframe_overlay"
DEFAULT_WORKFLOW = "keyframe_pair_ltx23_basic"
DEFAULT_MODEL_PROFILE = "ltx23_fp8"
DEFAULT_NEGATIVE_PROMPT = "text, subtitles, captions, watermark, logo, UI text, blurry face, distorted face, bad hands, extra fingers, low quality, flicker"

MAX_I2V_SEGMENT_DURATION = 4.5
MIN_I2V_SEGMENT_DURATION = 2.5
TARGET_I2V_SEGMENT_DURATION = 4.0
LONG_SHOT_THRESHOLD = 5.5


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def text_from(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("hint", "direction", "summary", "visual", "style", "motion", "prompt"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
        if parts:
            return " ".join(parts)
    return ""


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def normalize_keyframes(keyframes: Any) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    if not isinstance(keyframes, list):
        return []
    for frame in keyframes:
        if not isinstance(frame, dict):
            continue
        path = str(frame.get("path") or "").strip()
        if not path:
            continue
        by_path[path] = dict(frame)
    return sorted(by_path.values(), key=lambda item: (safe_float(item.get("time"), 1_000_000.0), str(item.get("path") or "")))


def nearest_keyframe(keyframes: list[dict[str, Any]], target: float) -> dict[str, Any] | None:
    if not keyframes:
        return None
    return min(keyframes, key=lambda item: (abs(safe_float(item.get("time"), 0.0) - target), str(item.get("path") or "")))


def choose_keyframe_pair(keyframes: list[dict[str, Any]], start_target: float, end_target: float) -> dict[str, Any] | None:
    if len(keyframes) < 2:
        return None
    first = nearest_keyframe(keyframes, start_target)
    last = nearest_keyframe(keyframes, end_target)
    if not first or not last:
        return None
    if first.get("path") == last.get("path"):
        alternatives = [frame for frame in keyframes if frame.get("path") != first.get("path")]
        if not alternatives:
            return None
        last = min(alternatives, key=lambda item: (abs(safe_float(item.get("time"), 0.0) - end_target), str(item.get("path") or "")))
    if safe_float(first.get("time"), 0.0) > safe_float(last.get("time"), 0.0):
        first, last = last, first
    return {
        "first_frame": compact_keyframe(first),
        "last_frame": compact_keyframe(last),
        "start_target": round(start_target, 3),
        "end_target": round(end_target, 3),
    }


def compact_keyframe(frame: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": safe_float(frame.get("time"), 0.0),
        "path": str(frame.get("path") or ""),
        "source": frame.get("source") or "",
    }


def split_chunks(start: float, duration: float) -> list[dict[str, float]]:
    if duration <= 0:
        return [{"index": 1, "start": start, "end": start, "duration": 0.0}]
    if duration <= LONG_SHOT_THRESHOLD:
        return [{"index": 1, "start": start, "end": start + duration, "duration": duration}]
    count = max(2, math.ceil(duration / TARGET_I2V_SEGMENT_DURATION))
    while count > 1 and duration / count < MIN_I2V_SEGMENT_DURATION:
        count -= 1
    chunk_duration = duration / count
    chunks: list[dict[str, float]] = []
    for index in range(count):
        chunk_start = start + index * chunk_duration
        chunk_end = start + duration if index == count - 1 else start + (index + 1) * chunk_duration
        chunks.append({"index": index + 1, "start": chunk_start, "end": chunk_end, "duration": chunk_end - chunk_start})
    return chunks


def output_dims(source_package: dict[str, Any]) -> tuple[int, int, int]:
    video = source_package.get("video") if isinstance(source_package.get("video"), dict) else {}
    width = safe_int(video.get("width"), 720) or 720
    height = safe_int(video.get("height"), 1280) or 1280
    fps = safe_int(video.get("fps"), 30) or 30
    return width, height, fps


def shot_prompt(shot: dict[str, Any]) -> str:
    parts = [
        text_from(shot.get("generation_hint")),
        text_from(shot.get("rebuild_direction")),
        "realistic vertical lifestyle video, natural motion, preserve reference composition rhythm, no text in base video",
    ]
    return " ".join(part for part in parts if part).strip()


def variants_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    variants = [item for item in (plan.get("variants") or []) if isinstance(item, dict)]
    if variants:
        return variants
    return [{"variant_id": DEFAULT_VARIANT_ID}]


def task(task_id: str, shot: dict[str, Any], variant_id: str, task_type: str, executor: str, depends_on: list[str], params: dict[str, Any], output: Any, input_payload: dict[str, Any] | None = None, workflow: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": task_id,
        "shot_id": shot["shot_id"],
        "variant_id": variant_id,
        "type": task_type,
        "executor": executor,
        "depends_on": depends_on,
        "params": params,
        "output": output,
    }
    if workflow:
        payload["workflow"] = workflow
    if input_payload is not None:
        payload["input"] = input_payload
    payload["cache_key"] = f"{task_type}:{stable_hash(payload)}"
    return payload


def build_static_tasks(shot: dict[str, Any], variant_id: str, keyframes: list[dict[str, Any]], width: int, height: int, fps: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    shot_id = shot["shot_id"]
    duration = max(safe_float(shot.get("duration"), 0.0), 0.1)
    frame = compact_keyframe(keyframes[0]) if keyframes else None
    base_id = f"task_{variant_id}_{shot_id}_static_base"
    overlay_id = f"task_{variant_id}_{shot_id}_overlay"
    composite_id = f"task_{variant_id}_{shot_id}_composite"
    base_output = f"assets/{variant_id}/{shot_id}/{shot_id}_base.mp4"
    overlay_output = f"assets/{variant_id}/{shot_id}/{shot_id}_overlay.webm"
    final_output = f"assets/{variant_id}/{shot_id}/{shot_id}_final.mp4"
    tasks = [
        task(
            base_id,
            shot,
            variant_id,
            "hyperframe_static_or_single_frame_overlay",
            "hyperframes",
            [],
            {"duration": round(duration, 3), "width": width, "height": height, "fps": fps, "motion": "subtle_pan_zoom"},
            base_output,
            {"image": frame["path"] if frame else "", "srt_text": ((shot.get("reference") or {}).get("srt_text") or "")},
        ),
        task(
            overlay_id,
            shot,
            variant_id,
            "hyperframe_overlay",
            "hyperframes",
            [base_id],
            {"duration": round(duration, 3), "width": width, "height": height, "fps": fps, "format": "webm", "transparent": True, "srt_text": ((shot.get("reference") or {}).get("srt_text") or ""), "subtitle_style": "bottom_bold_keyword_highlight"},
            overlay_output,
        ),
        task(
            composite_id,
            shot,
            variant_id,
            "video_composite",
            "ffmpeg",
            [base_id, overlay_id],
            {"duration": round(duration, 3), "width": width, "height": height, "fps": fps},
            final_output,
            {"base_video": base_output, "overlay": overlay_output},
        ),
    ]
    summary = {
        "selected_mode": "hyperframe_static_or_single_frame_overlay",
        "selected_keyframe_pair": None,
        "candidate_keyframe_pairs": [],
        "segments": [],
        "tasks": [item["task_id"] for item in tasks],
        "output": final_output,
    }
    return tasks, summary


def build_keyframe_pair_tasks(shot: dict[str, Any], variant_id: str, keyframes: list[dict[str, Any]], width: int, height: int, fps: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    shot_id = shot["shot_id"]
    start = safe_float(shot.get("start"), 0.0)
    duration = max(safe_float(shot.get("duration"), 0.0), 0.1)
    prompt = shot_prompt(shot)
    chunks = split_chunks(start, duration)
    tasks: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    i2v_task_ids: list[str] = []
    segment_outputs: list[str] = []
    candidate_pairs: list[dict[str, Any]] = []

    for chunk in chunks:
        ratio_start = 0.15 if len(chunks) == 1 else 0.10
        ratio_end = 0.85 if len(chunks) == 1 else 0.90
        pair = choose_keyframe_pair(keyframes, chunk["start"] + chunk["duration"] * ratio_start, chunk["start"] + chunk["duration"] * ratio_end)
        if pair is None:
            continue
        segment_index = safe_int(chunk["index"], len(segments) + 1)
        segment_id = f"segment_{segment_index:03d}"
        task_id = f"task_{variant_id}_{shot_id}_seg_{segment_index:03d}_i2v"
        output = f"assets/{variant_id}/{shot_id}/segments/{segment_id}_base.mp4"
        input_payload = {"first_frame": pair["first_frame"]["path"], "last_frame": pair["last_frame"]["path"]}
        params = {
            "prompt": prompt,
            "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
            "width": width,
            "height": height,
            "fps": 24,
            "duration": round(chunk["duration"], 3),
            "model_profile": DEFAULT_MODEL_PROFILE,
            "guide_strength": 0.7,
        }
        tasks.append(task(task_id, shot, variant_id, "keyframe_pair_i2v_segment", "comfyui", [], params, output, input_payload, DEFAULT_WORKFLOW))
        i2v_task_ids.append(task_id)
        segment_outputs.append(output)
        segment = {
            "segment_id": segment_id,
            "start": round(chunk["start"], 3),
            "end": round(chunk["end"], 3),
            "duration": round(chunk["duration"], 3),
            "selected_keyframe_pair": pair,
            "task_id": task_id,
            "output": output,
        }
        segments.append(segment)
        candidate_pairs.append(pair)

    if not tasks:
        return build_static_tasks(shot, variant_id, keyframes, width, height, fps)

    if len(segment_outputs) == 1:
        base_video = segment_outputs[0]
        base_depends_on = i2v_task_ids
    else:
        concat_id = f"task_{variant_id}_{shot_id}_base_concat"
        base_video = f"assets/{variant_id}/{shot_id}/{shot_id}_base.mp4"
        tasks.append(task(concat_id, shot, variant_id, "base_video_concat", "ffmpeg", i2v_task_ids, {"duration": round(duration, 3), "fps": 24}, base_video, {"segments": segment_outputs}))
        base_depends_on = [concat_id]

    overlay_id = f"task_{variant_id}_{shot_id}_overlay"
    composite_id = f"task_{variant_id}_{shot_id}_composite"
    overlay_output = f"assets/{variant_id}/{shot_id}/{shot_id}_overlay.webm"
    final_output = f"assets/{variant_id}/{shot_id}/{shot_id}_final.mp4"
    tasks.append(
        task(
            overlay_id,
            shot,
            variant_id,
            "hyperframe_overlay",
            "hyperframes",
            base_depends_on,
            {"duration": round(duration, 3), "width": width, "height": height, "fps": fps, "format": "webm", "transparent": True, "srt_text": ((shot.get("reference") or {}).get("srt_text") or ""), "subtitle_style": "bottom_bold_keyword_highlight"},
            overlay_output,
        )
    )
    tasks.append(
        task(
            composite_id,
            shot,
            variant_id,
            "video_composite",
            "ffmpeg",
            [*base_depends_on, overlay_id],
            {"duration": round(duration, 3), "width": width, "height": height, "fps": fps},
            final_output,
            {"base_video": base_video, "overlay": overlay_output},
        )
    )
    summary = {
        "selected_mode": DEFAULT_MODE,
        "selected_keyframe_pair": segments[0]["selected_keyframe_pair"],
        "candidate_keyframe_pairs": candidate_pairs,
        "segments": segments,
        "tasks": [item["task_id"] for item in tasks],
        "output": final_output,
    }
    return tasks, summary


def production_candidates(keyframe_count: int, include_debug_reuse_reference: bool) -> list[dict[str, Any]]:
    candidates = [
        {"mode": DEFAULT_MODE, "enabled": keyframe_count >= 2, "reason": "Uses saved first/last keyframes with chunking for stable shot-length video."},
        {"mode": "hyperframe_static_or_single_frame_overlay", "enabled": keyframe_count < 2, "reason": "Fallback for very short shots or shots with fewer than two saved keyframes."},
        {"mode": "image_pair_transform_then_keyframe_pair_i2v", "enabled": False, "reason": "Reserved for GPT/Gemini/Qwen pair contact sheet enhancement."},
        {"mode": "single_keyframe_i2v_with_hyperframe_overlay", "enabled": False, "reason": "Fallback only; current data supports keyframe pairs for most shots."},
        {"mode": "text_to_video_with_hyperframe_overlay", "enabled": False, "reason": "Fallback for shots without usable visual references."},
    ]
    if include_debug_reuse_reference:
        candidates.append({"mode": "reuse_reference_clip", "enabled": True, "debug_only": True, "reason": "Explicit smoke-test mode only; not a production candidate."})
    return candidates


def build_asset_tasks(shot_plan: dict[str, Any], source_package: dict[str, Any], include_debug_reuse_reference: bool) -> dict[str, Any]:
    width, height, fps = output_dims(source_package)
    variants = variants_from_plan(shot_plan)
    all_tasks: list[dict[str, Any]] = []
    shot_entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    for variant in variants:
        variant_id = str(variant.get("variant_id") or DEFAULT_VARIANT_ID).strip() or DEFAULT_VARIANT_ID
        for index, shot in enumerate([item for item in (shot_plan.get("shots") or []) if isinstance(item, dict)], start=1):
            shot = dict(shot)
            shot.setdefault("shot_id", f"shot_{index:03d}")
            reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
            keyframes = normalize_keyframes(reference.get("keyframes"))
            if len(keyframes) >= 2 and safe_float(shot.get("duration"), 0.0) >= 0.5:
                tasks, summary = build_keyframe_pair_tasks(shot, variant_id, keyframes, width, height, fps)
            else:
                tasks, summary = build_static_tasks(shot, variant_id, keyframes, width, height, fps)
                warnings.append(f"{variant_id}/{shot['shot_id']} uses static fallback because it has {len(keyframes)} saved keyframe(s) or very short duration.")
            all_tasks.extend(tasks)
            shot_entries.append(
                {
                    "variant_id": variant_id,
                    "shot_id": shot["shot_id"],
                    "source_segment_id": shot.get("source_segment_id") or "",
                    "duration": safe_float(shot.get("duration"), 0.0),
                    "production_candidates": production_candidates(len(keyframes), include_debug_reuse_reference),
                    "selected_mode": summary["selected_mode"],
                    "selected_keyframe_pair": summary["selected_keyframe_pair"],
                    "candidate_keyframe_pairs": summary["candidate_keyframe_pairs"],
                    "segments": summary["segments"],
                    "tasks": summary["tasks"],
                    "fallback_modes": ["hyperframe_static_or_single_frame_overlay", "single_keyframe_i2v_with_hyperframe_overlay", "text_to_video_with_hyperframe_overlay", "manual_upload_video_with_hyperframe_overlay"],
                    "output": summary["output"],
                }
            )
    return {
        "version": 1,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "shot_plan_path": "rebuild_shot_plan.json",
            "source_package_path": "source_package.json",
            "mode": DEFAULT_MODE,
        },
        "defaults": {
            "width": width,
            "height": height,
            "fps": fps,
            "max_i2v_segment_duration": MAX_I2V_SEGMENT_DURATION,
            "min_i2v_segment_duration": MIN_I2V_SEGMENT_DURATION,
            "target_i2v_segment_duration": TARGET_I2V_SEGMENT_DURATION,
            "workflow": DEFAULT_WORKFLOW,
            "model_profile": DEFAULT_MODEL_PROFILE,
            "reuse_reference_clip": "debug_only" if include_debug_reuse_reference else "disabled",
        },
        "task": shot_plan.get("task") or {},
        "variants": variants,
        "shots": shot_entries,
        "tasks": all_tasks,
        "validation": {"status": "passed" if all_tasks else "failed", "warnings": warnings},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build asset_tasks.json from saved rebuild_shot_plan.json without executing generation.")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--shot-plan", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--output", default="asset_tasks.json")
    parser.add_argument("--include-debug-reuse-reference", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    try:
        shot_plan = read_json(workspace / args.shot_plan)
        source_package = read_json(workspace / args.source_package)
        asset_tasks = build_asset_tasks(shot_plan, source_package, args.include_debug_reuse_reference)
        write_json(workspace / args.output, asset_tasks)
        result = {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "status": "completed",
            "output": args.output,
            "shot_count": len(asset_tasks.get("shots") or []),
            "task_count": len(asset_tasks.get("tasks") or []),
            "warning_count": len((asset_tasks.get("validation") or {}).get("warnings") or []),
        }
    except Exception as exc:
        result = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "failed", "message": str(exc)}
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
