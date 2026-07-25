from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


TOOL_NAME = "04_03_StoryBoardQuick"
TOOL_VERSION = "0.1.0"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
TOOL_DIR_NAME = "S7_04_03_StoryBoardQuick"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_REWRITTEN_ITEMS_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_6_rewritten_srt_items.json"
WORKING_PARAMS_REL = f"{TOOL_DIR_NAME}/Working/InputParams_storyboard_quick_config.json"
WORKING_AUDIT_REL = f"{TOOL_DIR_NAME}/Working/State_grouping_audit.json"
OUTPUT_STORYBOARD_REL = f"{TOOL_DIR_NAME}/Output/srt_storyboard.json"
OUTPUT_AUDIT_REL = f"{TOOL_DIR_NAME}/Output/grouping_audit.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SESSION_REWRITTEN_ITEMS_REL = "SessionOutput/subtitle/rewritten_srt_items.json"
SESSION_STORYBOARD_DIR_REL = "SessionOutput/storyboard"
SESSION_STORYBOARD_REL = f"{SESSION_STORYBOARD_DIR_REL}/srt_storyboard.json"
SESSION_STORYBOARD_ASSETS_IMAGES_DIR_REL = f"{SESSION_STORYBOARD_DIR_REL}/assets/images"
SESSION_STORYBOARD_ASSETS_VIDEOS_DIR_REL = f"{SESSION_STORYBOARD_DIR_REL}/assets/videos"
SESSION_STORYBOARD_WORKING_DIR_REL = f"{SESSION_STORYBOARD_DIR_REL}/Working"
LEGACY_STORYBOARD_LAYOUT_DIRS = (
    f"{SESSION_STORYBOARD_DIR_REL}/shots",
    f"{SESSION_STORYBOARD_DIR_REL}/scenes",
)
DEFAULT_CONFIG = {
    "enabled": True,
    "target_scene_seconds": 8.0,
    "target_shot_seconds": 16.0,
    "split_tolerance_seconds": 2.0,
    "language_boundary_mode": "balanced",
}
HARD_ENDINGS = tuple("。！？!?…")
SOFT_ENDINGS = tuple("，,；;：:")
TRAILING_CONNECTORS = ("但是", "因为", "所以", "然后", "接下来", "如果", "只要", "而且", "并且", "同时", "不过")
LEADING_CONNECTORS = ("所以", "因此", "但是", "那么", "然后", "也就是说", "换句话说", "接下来", "不过")
SECRET_PATTERNS = (
    "postgresql://",
    "postgresql+psycopg://",
    "password",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "bearer ",
    "cookie",
)


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Args:
    workspace: str
    target_scene_seconds: Optional[float]
    target_shot_seconds: Optional[float]
    split_tolerance_seconds: Optional[float]
    language_boundary_mode: str
    force: bool
    resume: bool
    print_json: bool


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return json_safe(value.tolist())
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def float_value(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def stable_hash(payload: Any) -> str:
    raw = json.dumps(json_safe(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser() if raw_workspace else Path.cwd()
    try:
        return workspace.resolve()
    except Exception:
        return workspace.absolute()


def validate_workspace(workspace: Path) -> None:
    if not workspace.exists():
        raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise BlockedError("workspace_not_directory", f"Workspace is not a directory: {workspace}")


def load_variables(workspace: Path) -> dict[str, Any]:
    path = workspace / VARIABLES_REL
    if not path.exists():
        raise BlockedError("variables_missing", f"Required SessionContext file is missing: {VARIABLES_REL}.")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    return payload


def load_rewritten_items(workspace: Path) -> dict[str, Any]:
    path = workspace / SESSION_REWRITTEN_ITEMS_REL
    if not path.exists():
        raise BlockedError("rewritten_srt_items_missing", f"Required rewritten SRT JSON is missing: {SESSION_REWRITTEN_ITEMS_REL}. Run 04_01_SRTRewrite.py first.")
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise BlockedError("rewritten_srt_items_invalid", f"{SESSION_REWRITTEN_ITEMS_REL} must contain a JSON object with items.")
    if not payload["items"]:
        raise BlockedError("rewritten_srt_items_empty", f"{SESSION_REWRITTEN_ITEMS_REL} contains no dialogue items.")
    return payload


def ensure_dirs(workspace: Path) -> None:
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        f"{TOOL_DIR_NAME}/Report",
        SESSION_STORYBOARD_DIR_REL,
        SESSION_STORYBOARD_ASSETS_IMAGES_DIR_REL,
        SESSION_STORYBOARD_ASSETS_VIDEOS_DIR_REL,
        SESSION_STORYBOARD_WORKING_DIR_REL,
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    for rel in (TOOL_DIR_NAME, SESSION_STORYBOARD_REL, *LEGACY_STORYBOARD_LAYOUT_DIRS):
        path = workspace / rel
        if path.exists():
            remove_path(path)
            result.setdefault("cleanup_actions", []).append({"path": rel, "action": "removed_for_force_rerun"})


def add_block(result: dict[str, Any], code: str, message: str) -> None:
    result["status"] = "blocked"
    result.setdefault("blocked_reasons", []).append({"code": code, "message": message})


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": False,
        "requires_model_calls": False,
        "model_call_policy": {
            "text_model": "not_used",
            "visual_model": "not_used",
            "prompt_dir": "not_created",
        },
        "inputs": {},
        "outputs": {},
        "counts": {},
        "algorithm_config": {},
        "created_files": [],
        "prepared_directories": [],
        "cleanup_actions": [],
        "warnings": [],
        "blocked_reasons": [],
        "force": bool(args.force),
        "resume": bool(args.resume),
        "updated_at": now_iso(),
    }


def normalize_config(args: Args, variables: dict[str, Any]) -> dict[str, Any]:
    raw = dict_value(variables.get("storyboard_quick_config"))

    def positive(key: str, cli_value: Optional[float], fallback: float) -> float:
        if cli_value is not None and cli_value > 0:
            return float(cli_value)
        parsed = float_value(raw.get(key))
        return parsed if parsed > 0 else fallback

    scene = positive("target_scene_seconds", args.target_scene_seconds, DEFAULT_CONFIG["target_scene_seconds"])
    shot = positive("target_shot_seconds", args.target_shot_seconds, DEFAULT_CONFIG["target_shot_seconds"])
    tolerance = positive("split_tolerance_seconds", args.split_tolerance_seconds, DEFAULT_CONFIG["split_tolerance_seconds"])
    mode = text_value(args.language_boundary_mode) or text_value(raw.get("language_boundary_mode")) or DEFAULT_CONFIG["language_boundary_mode"]
    mode = mode.lower()
    if mode not in {"strict", "balanced", "loose"}:
        mode = DEFAULT_CONFIG["language_boundary_mode"]
    return {
        "enabled": raw.get("enabled") is not False,
        "target_scene_seconds": max(1.0, float(scene)),
        "target_shot_seconds": max(1.0, float(shot)),
        "split_tolerance_seconds": max(0.0, float(tolerance)),
        "language_boundary_mode": mode,
        "source": text_value(raw.get("source")) or "SessionContext/Variables.json:storyboard_quick_config",
    }


def validate_input_items(items: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    previous_start = -1.0
    for index, item in enumerate(items, 1):
        srt_id = text_value(item.get("srt_id"))
        if not srt_id:
            raise BlockedError("input_srt_id_missing", f"Input item #{index} is missing srt_id.")
        if srt_id in seen:
            raise BlockedError("input_srt_id_duplicate", f"Input contains duplicate srt_id: {srt_id}")
        seen.add(srt_id)
        if not text_value(item.get("dialogue")):
            raise BlockedError("input_dialogue_missing", f"Input item {srt_id} has empty dialogue.")
        start = float_value(item.get("start"))
        end = float_value(item.get("end"))
        if end < start:
            raise BlockedError("input_time_invalid", f"Input item {srt_id} ends before it starts.")
        if start < previous_start:
            raise BlockedError("input_time_order_invalid", f"Input item {srt_id} starts before the previous item.")
        previous_start = start


def item_duration(items: list[dict[str, Any]], start_index: int, end_index: int) -> float:
    start = float_value(items[start_index].get("start"))
    end = float_value(items[end_index].get("end"))
    return max(0.0, end - start)


def boundary_score(current_text: str, next_text: str, duration: float, config: dict[str, Any], forced: bool = False) -> tuple[float, dict[str, Any]]:
    target = float(config["target_scene_seconds"])
    tolerance = float(config["split_tolerance_seconds"])
    min_duration = max(0.0, target - tolerance)
    max_duration = target + tolerance
    mode = str(config.get("language_boundary_mode") or "balanced")
    stripped = current_text.strip()
    next_stripped = next_text.strip()
    hard = stripped.endswith(HARD_ENDINGS)
    soft = stripped.endswith(SOFT_ENDINGS)
    connector_penalty = 0.0
    if any(stripped.endswith(word) for word in TRAILING_CONNECTORS):
        connector_penalty += 12.0 if mode != "loose" else 7.0
    if any(next_stripped.startswith(word) for word in LEADING_CONNECTORS):
        connector_penalty += 8.0 if mode == "strict" else 4.0
    punctuation = 24.0 if hard else 11.0 if soft else 3.0
    if mode == "strict" and hard:
        punctuation += 8.0
    if mode == "loose" and not hard:
        punctuation += 3.0
    duration_score = max(0.0, 36.0 - abs(duration - target) * 5.0)
    if duration < min_duration:
        duration_score -= (min_duration - duration) * 7.0
    if duration > max_duration:
        duration_score -= (duration - max_duration) * 3.0
    score = duration_score + punctuation - connector_penalty - (20.0 if forced else 0.0)
    return score, {
        "hard_sentence_boundary": hard,
        "soft_pause_boundary": soft,
        "connector_penalty": round(connector_penalty, 3),
        "duration_score": round(duration_score, 3),
        "boundary_score": round(punctuation, 3),
        "forced": forced,
    }


def build_scene_groups(items: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target = float(config["target_scene_seconds"])
    tolerance = float(config["split_tolerance_seconds"])
    min_duration = max(0.0, target - tolerance)
    max_duration = target + tolerance
    groups: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    index = 0
    while index < len(items):
        best: tuple[float, int, dict[str, Any]] | None = None
        forced: tuple[float, int, dict[str, Any]] | None = None
        cursor = index
        while cursor < len(items):
            duration = item_duration(items, index, cursor)
            next_text = text_value(items[cursor + 1].get("dialogue")) if cursor + 1 < len(items) else ""
            score, detail = boundary_score(text_value(items[cursor].get("dialogue")), next_text, duration, config)
            if duration >= min_duration or cursor == len(items) - 1:
                candidate = (score, cursor, detail)
                if best is None or candidate[0] > best[0]:
                    best = candidate
            if duration >= max_duration:
                forced_score, forced_detail = boundary_score(text_value(items[cursor].get("dialogue")), next_text, duration, config, forced=True)
                forced = (forced_score, cursor, forced_detail)
                break
            cursor += 1
        selected = best or forced
        if selected is None:
            selected = (0.0, min(len(items) - 1, index), {"forced": True})
        score, end_index, detail = selected
        if forced and best is None:
            score, end_index, detail = forced
        ids = [text_value(item.get("srt_id")) for item in items[index : end_index + 1]]
        duration = item_duration(items, index, end_index)
        group = {"start_index": index, "end_index": end_index, "srt_ids": ids, "duration": duration}
        groups.append(group)
        audit.append({
            "scene_number": len(groups),
            "srt_ids": ids,
            "duration": round(duration, 3),
            "score": round(score, 3),
            "boundary": detail,
        })
        index = end_index + 1

    if len(groups) > 1 and groups[-1]["duration"] < min_duration:
        last = groups.pop()
        prev = groups[-1]
        prev["end_index"] = last["end_index"]
        prev["srt_ids"].extend(last["srt_ids"])
        prev["duration"] = item_duration(items, prev["start_index"], prev["end_index"])
        audit.append({
            "action": "merged_short_tail_scene",
            "merged_srt_ids": last["srt_ids"],
            "target_scene_seconds": target,
            "min_scene_seconds": min_duration,
        })
    return groups, audit


def build_shot_groups(scene_groups: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
    target = float(config["target_shot_seconds"])
    tolerance = float(config["split_tolerance_seconds"])
    min_duration = max(0.0, target - tolerance)
    max_duration = target + tolerance
    groups: list[list[dict[str, Any]]] = []
    audit: list[dict[str, Any]] = []
    index = 0
    while index < len(scene_groups):
        best_index: int | None = None
        best_score = -999999.0
        duration = 0.0
        cursor = index
        while cursor < len(scene_groups):
            duration += float(scene_groups[cursor]["duration"])
            score = 50.0 - abs(duration - target) * 4.0
            if duration >= min_duration and score > best_score:
                best_index = cursor
                best_score = score
            if duration >= max_duration:
                if best_index is None:
                    best_index = cursor
                    best_score = score - 20.0
                break
            cursor += 1
        if best_index is None:
            best_index = len(scene_groups) - 1
        shot_scenes = scene_groups[index : best_index + 1]
        groups.append(shot_scenes)
        audit.append({
            "shot_number": len(groups),
            "scene_numbers": list(range(index + 1, best_index + 2)),
            "duration": round(sum(float(scene["duration"]) for scene in shot_scenes), 3),
        })
        index = best_index + 1

    if len(groups) > 1 and sum(float(scene["duration"]) for scene in groups[-1]) < min_duration:
        tail = groups.pop()
        groups[-1].extend(tail)
        audit.append({"action": "merged_short_tail_shot", "tail_scene_count": len(tail), "min_shot_seconds": min_duration})
    return groups, audit


def item_index(source_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {text_value(item.get("srt_id")): item for item in source_items}


def time_span(ids: list[str], by_id: dict[str, dict[str, Any]]) -> tuple[float, float, float]:
    starts = [float_value(by_id[srt_id].get("start")) for srt_id in ids if srt_id in by_id]
    ends = [float_value(by_id[srt_id].get("end")) for srt_id in ids if srt_id in by_id]
    if not starts or not ends:
        return 0.0, 0.0, 0.0
    start = starts[0]
    end = ends[-1]
    return start, end, max(0.0, end - start)


def dialogue_items_for(ids: list[str], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, srt_id in enumerate(ids):
        item = by_id.get(srt_id)
        if not item:
            continue
        items.append({
            "srt_id": srt_id,
            "dialogue": text_value(item.get("dialogue")),
            "start": item.get("start"),
            "end": item.get("end"),
            "duration": item.get("duration"),
            "image_path": text_value(item.get("image_path")) if index == 0 else "",
        })
    return items


def scene_key_frame_for(ids: list[str], by_id: dict[str, dict[str, Any]]) -> list[str]:
    for srt_id in ids:
        image_path = text_value(by_id.get(srt_id, {}).get("image_path"))
        if image_path:
            return [image_path]
    return []


def shot_key_frames_for(scenes: list[dict[str, Any]]) -> list[str]:
    frames: list[str] = []
    seen: set[str] = set()
    for scene in scenes:
        for image_path in scene.get("key_frame_paths") or []:
            image_text = text_value(image_path)
            if image_text and image_text not in seen:
                frames.append(image_text)
                seen.add(image_text)
    return frames


def empty_working_assets() -> dict[str, Any]:
    return {
        "audio": {"slot": "Audio_Final", "path": ""},
        "images": [{"slot": "Image_New", "path": ""}, {"slot": "Image_02", "path": ""}],
        "video": {"slot": "Video_Final", "path": ""},
    }


def business_context(variables: dict[str, Any]) -> dict[str, str]:
    explicit = dict_value(variables.get("business_context"))
    keys = ("industry", "persona", "target_audience", "product_info", "constraints", "video_formula")
    return {key: text_value(explicit.get(key) or variables.get(key)) for key in keys}


def build_final_payload(variables: dict[str, Any], source_items: list[dict[str, Any]], config: dict[str, Any], scene_groups: list[dict[str, Any]], shot_groups: list[list[dict[str, Any]]], source_signature: dict[str, str]) -> dict[str, Any]:
    by_id = item_index(source_items)
    final_shots: list[dict[str, Any]] = []
    scene_global_number = 0
    for shot_number, shot_group in enumerate(shot_groups, 1):
        shot_id = f"shot_{shot_number:03d}"
        final_scenes: list[dict[str, Any]] = []
        shot_srt_ids: list[str] = []
        for scene_group in shot_group:
            scene_global_number += 1
            scene_id = f"scene_{scene_global_number:03d}"
            scene_srt_ids = list(scene_group["srt_ids"])
            start, end, duration = time_span(scene_srt_ids, by_id)
            asset_key = f"{shot_id}_{scene_id}"
            final_scenes.append({
                "scene_id": scene_id,
                "title": scene_id,
                "summary": "",
                "start": start,
                "end": end,
                "duration": duration,
                "srt_ids": scene_srt_ids,
                "dialogue_items": dialogue_items_for(scene_srt_ids, by_id),
                "key_frame_paths": scene_key_frame_for(scene_srt_ids, by_id),
                "asset_key": asset_key,
                "working_assets": empty_working_assets(),
            })
            shot_srt_ids.extend(scene_srt_ids)
        start, end, duration = time_span(shot_srt_ids, by_id)
        final_shots.append({
            "shot_id": shot_id,
            "title": shot_id,
            "formula_stage": "",
            "summary": "",
            "start": start,
            "end": end,
            "duration": duration,
            "srt_ids": shot_srt_ids,
            "key_frame_paths": shot_key_frames_for(final_scenes),
            "scenes": final_scenes,
        })
    return {
        "schema_version": "analysis_v1_srt_storyboard_0.2",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "storyboard_mode": "quick",
        "source_items_path": SESSION_REWRITTEN_ITEMS_REL,
        "prompt_source": "SessionContext/Variables.json:storyboard_quick_config",
        "business_context": business_context(variables),
        "video_formula": business_context(variables).get("video_formula", ""),
        "model": {"provider": "none", "model": "deterministic_storyboard_quick", "source": "algorithm"},
        "identity_policy": "dialogue_items_are_preserved_per_srt_under_scene; shot_scene_grouping_only",
        "algorithm_config": config,
        "source_signature": source_signature,
        "shots": final_shots,
        "created_at": now_iso(),
    }


def validate_final_payload(payload: dict[str, Any], source_items: list[dict[str, Any]]) -> None:
    expected_ids = [text_value(item.get("srt_id")) for item in source_items]
    actual_ids: list[str] = []
    for shot in payload.get("shots") or []:
        for scene in shot.get("scenes") or []:
            actual_ids.extend([text_value(srt_id) for srt_id in scene.get("srt_ids") or []])
    if actual_ids != expected_ids:
        missing = [srt_id for srt_id in expected_ids if srt_id not in actual_ids]
        duplicates = sorted({srt_id for srt_id in actual_ids if actual_ids.count(srt_id) > 1})
        raise BlockedError("storyboard_quick_output_invalid", f"Output srt_id coverage/order invalid. missing={missing[:5]} duplicates={duplicates[:5]}")


def reusable_existing_storyboard(workspace: Path, source_signature: dict[str, str], force: bool) -> dict[str, Any] | None:
    if force:
        return None
    path = workspace / SESSION_STORYBOARD_REL
    if not path.exists():
        return None
    payload = read_json(path)
    if not isinstance(payload, dict):
        return None
    if text_value(payload.get("tool")) != TOOL_NAME:
        return None
    if dict_value(payload.get("source_signature")) != source_signature:
        return None
    return payload


def scan_for_sensitive_output(payload: dict[str, Any]) -> list[dict[str, str]]:
    text = json.dumps(payload, ensure_ascii=False).lower()
    return [{"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"} for pattern in SECRET_PATTERNS if pattern in text]


def run_storyboard_quick(workspace: Path, args: Args, variables: dict[str, Any], rewritten_payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    source_items = [item for item in rewritten_payload.get("items", []) if isinstance(item, dict)]
    validate_input_items(source_items)
    config = normalize_config(args, variables)
    source_signature = {
        "rewritten_srt_items_sha256": stable_hash(rewritten_payload),
        "algorithm_config_sha256": stable_hash(config),
    }
    write_json(workspace / WORKING_VARIABLES_REL, variables)
    write_json(workspace / WORKING_REWRITTEN_ITEMS_REL, rewritten_payload)
    write_json(workspace / WORKING_PARAMS_REL, config)
    reusable = reusable_existing_storyboard(workspace, source_signature, force=args.force or not args.resume)
    if reusable:
        result["warnings"].append({"code": "reused_completed_output", "message": "Existing 04_03 StoryBoardQuick output was reused."})
        final_payload = reusable
        audit = {"reused": True, "source_signature": source_signature, "algorithm_config": config}
    else:
        scene_groups, scene_audit = build_scene_groups(source_items, config)
        shot_groups, shot_audit = build_shot_groups(scene_groups, config)
        final_payload = build_final_payload(variables, source_items, config, scene_groups, shot_groups, source_signature)
        validate_final_payload(final_payload, source_items)
        audit = {
            "schema_version": "analysis_v1_storyboard_quick_audit_0.1",
            "tool": TOOL_NAME,
            "algorithm_config": config,
            "source_signature": source_signature,
            "scene_boundaries": scene_audit,
            "shot_boundaries": shot_audit,
            "created_at": now_iso(),
        }
        write_json(workspace / OUTPUT_STORYBOARD_REL, final_payload)
        write_json(workspace / SESSION_STORYBOARD_REL, final_payload)
    write_json(workspace / WORKING_AUDIT_REL, audit)
    write_json(workspace / OUTPUT_AUDIT_REL, audit)

    scene_count = sum(len(shot.get("scenes") or []) for shot in final_payload.get("shots") or [])
    forced_count = sum(1 for item in (audit.get("scene_boundaries") or []) if dict_value(item.get("boundary")).get("forced"))
    result["status"] = "completed"
    result["inputs"] = {
        "variables": VARIABLES_REL,
        "rewritten_srt_items": SESSION_REWRITTEN_ITEMS_REL,
    }
    result["outputs"] = {
        "srt_storyboard": SESSION_STORYBOARD_REL,
        "grouping_audit": OUTPUT_AUDIT_REL,
    }
    result["counts"] = {
        "input_items": len(source_items),
        "shots": len(final_payload.get("shots") or []),
        "scenes": scene_count,
        "working_asset_scenes": scene_count,
        "forced_boundaries": forced_count,
    }
    result["algorithm_config"] = config
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_REWRITTEN_ITEMS_REL,
        WORKING_PARAMS_REL,
        WORKING_AUDIT_REL,
        OUTPUT_STORYBOARD_REL,
        OUTPUT_AUDIT_REL,
        SESSION_STORYBOARD_REL,
        REPORT_RESULT_REL,
    ]
    return final_payload


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    try:
        validate_workspace(workspace)
        if args.force:
            force_reset(workspace, result)
        ensure_dirs(workspace)
        for rel in (
            f"{TOOL_DIR_NAME}/Working",
            f"{TOOL_DIR_NAME}/Output",
            f"{TOOL_DIR_NAME}/Report",
            SESSION_STORYBOARD_DIR_REL,
            SESSION_STORYBOARD_ASSETS_IMAGES_DIR_REL,
            SESSION_STORYBOARD_ASSETS_VIDEOS_DIR_REL,
            SESSION_STORYBOARD_WORKING_DIR_REL,
        ):
            result["prepared_directories"].append(rel)
        variables = load_variables(workspace)
        rewritten_payload = load_rewritten_items(workspace)
        final_payload = run_storyboard_quick(workspace, args, variables, rewritten_payload, result)
        result["warnings"].extend(scan_for_sensitive_output(final_payload))
        if result["warnings"]:
            sensitive = [item for item in result["warnings"] if item.get("code") == "sensitive_output_pattern_detected"]
            if sensitive:
                result["status"] = "failed"
                result.setdefault("blocked_reasons", []).append({"code": "sensitive_output_detected", "message": "Sensitive-looking content detected in tool output."})
    except BlockedError as exc:
        add_block(result, exc.code, exc.message)
    except PermissionError as exc:
        add_block(result, "workspace_permission_denied", f"Cannot read/write Analysis_V1 workspace. Original error: {exc}")
    except Exception as exc:
        result["status"] = "failed"
        result["warnings"].append({"code": "unexpected_error", "message": str(exc)})
    result["updated_at"] = now_iso()
    try:
        if workspace.exists() and workspace.is_dir():
            (workspace / f"{TOOL_DIR_NAME}/Report").mkdir(parents=True, exist_ok=True)
            write_json(workspace / REPORT_RESULT_REL, result)
    except Exception as exc:
        result["warnings"].append({"code": "result_write_failed", "message": str(exc)})
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Build an Analysis_V1 SRT StoryBoard with deterministic language-aware Shot / Scene grouping.")
    parser.add_argument("--workspace", default="", help="Analysis_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--target-scene-seconds", type=float)
    parser.add_argument("--target-shot-seconds", type=float)
    parser.add_argument("--split-tolerance-seconds", type=float)
    parser.add_argument("--language-boundary-mode", default="", choices=("", "strict", "balanced", "loose"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        target_scene_seconds=ns.target_scene_seconds,
        target_shot_seconds=ns.target_shot_seconds,
        split_tolerance_seconds=ns.split_tolerance_seconds,
        language_boundary_mode=str(ns.language_boundary_mode or ""),
        force=bool(ns.force),
        resume=bool(ns.resume),
        print_json=bool(ns.print_json),
    )


def main(argv: list[str] | None = None) -> int:
    cli_args = argv if argv is not None else sys.argv[1:]
    if "--tool-session-root" in cli_args:
        try:
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge
        except ModuleNotFoundError:
            repo_root = str(Path(__file__).resolve().parents[2])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge

        framework_exit = maybe_run_framework_bridge(cli_args, script_path=Path(__file__), tool_name=TOOL_NAME)
        if framework_exit is not None:
            return framework_exit

    args = parse_args(cli_args)
    result = run(args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} {result['status']}: {result.get('outputs', {}).get('srt_storyboard', '')}")
    return 0 if result.get("status") == "completed" else 2 if result.get("status") == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
