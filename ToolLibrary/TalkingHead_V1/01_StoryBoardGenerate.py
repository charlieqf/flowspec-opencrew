from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any


WORKFLOW_ID = "person_talking_head_v1"
TOOL_NAME = "01_StoryBoardGenerate"
TOOL_VERSION = "0.2.0"
VARIABLES_REL = "SessionContext/Variables.json"
REWRITTEN_REL = "SessionOutput/subtitle/rewritten_srt_items.json"
STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
EDIT_REL = "SessionOutput/storyboard/koubo_storyboard_edit.json"
OUTPUT_REL = "S2_01_StoryBoardGenerate/Output/srt_storyboard.json"
REPORT_REL = "S2_01_StoryBoardGenerate/Report/Result.json"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_ms() -> int:
    return int(time.time() * 1000)


def text(value: Any, fallback: str = "") -> str:
    result = str(value if value is not None else "").strip()
    return result or fallback


def number(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else fallback
    except Exception:
        return fallback


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_key(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", text(value)).strip("_")
    return cleaned or fallback


def rewritten_items(workspace: Path) -> list[dict[str, Any]]:
    payload = read_json(workspace / REWRITTEN_REL, {}) or {}
    items = [item for item in list_value(payload.get("items")) if isinstance(item, dict)]
    result: list[dict[str, Any]] = []
    cursor = 0.0
    for index, item in enumerate(items, start=1):
        dialogue = text(item.get("dialogue") or item.get("text"))
        if not dialogue:
            continue
        start = number(item.get("start"), cursor)
        duration = number(item.get("duration"), 0.0)
        end = number(item.get("end"), start + duration)
        if end <= start:
            end = start + max(duration, 0.2)
        duration = max(0.2, end - start)
        normalized = {
            **item,
            "index": int(item.get("index") or index),
            "srt_id": text(item.get("srt_id") or item.get("id"), f"srt_{index:04d}"),
            "dialogue": dialogue,
            "start": round(start, 3),
            "end": round(start + duration, 3),
            "duration": round(duration, 3),
        }
        result.append(normalized)
        cursor = float(normalized["end"])
    return result


def empty_assets() -> dict[str, Any]:
    return {
        "audio": {"slot": "Audio_Final", "source_type": "", "path": ""},
        "images": [
            {"slot": "Image_New", "source_type": "", "path": ""},
            {"slot": "Image_02", "source_type": "", "path": ""},
        ],
        "video": {"slot": "Video_Final", "source_type": "", "path": ""},
    }


def source_dialogues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dialogues: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        srt_ids = [text(value) for value in list_value(item.get("srt_ids")) if text(value)]
        srt_id = text(item.get("srt_id"), srt_ids[0] if srt_ids else f"srt_{index:04d}")
        if not srt_ids:
            srt_ids = [srt_id] if srt_id else []
        group_id = text(item.get("dialogue_group_id") or item.get("group_id"), f"dialogue_{index:03d}")
        asset_key = safe_key(text(item.get("dialogue_asset_key") or group_id), f"talking_head_{index:04d}")
        dialogues.append({
            "srt_id": srt_id,
            "srt_ids": srt_ids,
            "dialogue_id": f"scene_001_dialogue_{index:03d}",
            "dialogue_asset_key": asset_key,
            "dialogue": text(item.get("dialogue") or item.get("text")),
            "start": round(number(item.get("start")), 3),
            "end": round(number(item.get("end")), 3),
            "duration": round(number(item.get("duration"), number(item.get("end")) - number(item.get("start"))), 3),
            "image_path": "",
            "key_frame_paths": [],
            "talking_head": {"enabled": True, "segment_policy": "merge_srt_to_single_video_length"},
            "video_plan": {"is_talking_head": True, "resource_strategy": "talking_head_only", "allow_cutaway": False},
            "working_assets": empty_assets(),
        })
    return dialogues


def source_storyboard(workspace: Path, items: list[dict[str, Any]], variables: dict[str, Any]) -> dict[str, Any]:
    dialogues = source_dialogues(items)
    start = number(dialogues[0].get("start")) if dialogues else 0.0
    end = number(dialogues[-1].get("end")) if dialogues else 0.0
    duration = max(0.0, end - start)
    scene = {
        "scene_id": "scene_001",
        "title": "人物口播",
        "summary": "单场景人物口播，逐句生成 Dialogue/Segment。",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(duration, 3),
        "srt_ids": [srt_id for item in dialogues for srt_id in list_value(item.get("srt_ids")) if text(srt_id)],
        "key_frame_paths": [],
        "working_assets": empty_assets(),
        "dialogue_items": dialogues,
    }
    shot = {
        "shot_id": "shot_001",
        "title": "人物口播",
        "formula_stage": "talking_head",
        "summary": "固定一个 Shot，一个 Scene。",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(duration, 3),
        "srt_ids": scene["srt_ids"],
        "key_frame_paths": [],
        "scenes": [scene],
    }
    storyboard_prompt = dict_value(variables.get("storyboard_prompt"))
    return {
        "schema_version": "analysis_v1_srt_storyboard_0.2",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workflow_id": WORKFLOW_ID,
        "source_type": "person_talking_head_storyboard",
        "resource_strategy": {"kind": "talking_head_only", "allow_cutaway": False},
        "storyboard_final_prompt": text(storyboard_prompt.get("final_prompt")),
        "structure_policy": "single_shot_single_scene_merge_srt_to_single_video_length",
        "shots": [shot],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def edit_storyboard(source: dict[str, Any], task_id: int = 0, session_id: int = 0) -> dict[str, Any]:
    shot = dict_value(list_value(source.get("shots"))[0] if list_value(source.get("shots")) else {})
    scene = dict_value(list_value(shot.get("scenes"))[0] if list_value(shot.get("scenes")) else {})
    dialogues = []
    for index, item in enumerate(list_value(scene.get("dialogue_items")), start=1):
        if not isinstance(item, dict):
            continue
        dialogues.append({
            "dialogue_id": text(item.get("dialogue_id"), f"scene_001_dialogue_{index:03d}"),
            "scene_id": "scene_001",
            "dialogue_index": index,
            "srt_id": text(item.get("srt_id")),
            "srt_ids": [text(value) for value in list_value(item.get("srt_ids")) if text(value)] or ([text(item.get("srt_id"))] if text(item.get("srt_id")) else []),
            "dialogue_asset_key": text(item.get("dialogue_asset_key"), f"talking_head_{index:04d}"),
            "text": text(item.get("dialogue") or item.get("text")),
            "start": round(number(item.get("start")), 3),
            "end": round(number(item.get("end")), 3),
            "duration": round(number(item.get("duration")), 3),
            "source_image_paths": [],
            "image_path": "",
            "bound_image_path": "",
            "talking_head": dict_value(item.get("talking_head")),
            "video_plan": dict_value(item.get("video_plan")),
            "working_assets": dict_value(item.get("working_assets")) or empty_assets(),
        })
    return {
        "schema_version": "koubo_storyboard_edit_0.1",
        "title": "人物口播故事版",
        "source_type": "person_talking_head_storyboard",
        "workflow_mode": WORKFLOW_ID,
        "analysis_task_id": int(task_id or 0),
        "analysis_session_id": int(session_id or 0),
        "source_path": STORYBOARD_REL,
        "created_from_tool": TOOL_NAME,
        "updated_at": now_ms(),
        "shots": [{
            "shot_id": "shot_001",
            "source_shot_id": "shot_001",
            "shot_name": text(shot.get("title"), "shot_001"),
            "formula_stage": "talking_head",
            "summary": text(shot.get("summary")),
            "start": round(number(shot.get("start")), 3),
            "end": round(number(shot.get("end")), 3),
            "duration": round(number(shot.get("duration")), 3),
            "scenes": [{
                "scene_id": "scene_001",
                "source_scene_id": "scene_001",
                "asset_key": "shot_001_scene_001",
                "scene_name": text(scene.get("title"), "scene_001"),
                "scene_index": 1,
                "start": round(number(scene.get("start")), 3),
                "end": round(number(scene.get("end")), 3),
                "duration": round(number(scene.get("duration")), 3),
                "summary": text(scene.get("summary")),
                "working_assets": dict_value(scene.get("working_assets")) or empty_assets(),
                "dialogues": dialogues,
            }],
        }],
    }


def run(workspace: Path, force: bool = False) -> dict[str, Any]:
    workspace = workspace.resolve()
    variables = read_json(workspace / VARIABLES_REL, {}) or {}
    if not isinstance(variables, dict):
        variables = {}
    if text(variables.get("workflow_id")) != WORKFLOW_ID:
        raise RuntimeError("请先运行 TalkingHead_V1/00 生成当前人物口播 Variables。")
    items = rewritten_items(workspace)
    if not items:
        raise RuntimeError(f"口播脚本改写结果为空，请先运行 04_01：{REWRITTEN_REL}")
    source = source_storyboard(workspace, items, variables)
    task_snapshot = dict_value(variables.get("task"))
    task_id = int(task_snapshot.get("task_id") or variables.get("task_id") or 0)
    session_id = int(task_snapshot.get("session_id") or variables.get("session_id") or 0)
    edit = edit_storyboard(source, task_id=task_id, session_id=session_id)
    write_json(workspace / STORYBOARD_REL, source)
    write_json(workspace / EDIT_REL, edit)
    write_json(workspace / OUTPUT_REL, source)
    variables.update({
        "workflow_id": WORKFLOW_ID,
        "talking_head_storyboard_generated": True,
        "talking_head_dialogue_count": len(items),
        "updated_at": now_iso(),
    })
    write_json(workspace / VARIABLES_REL, variables)
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workflow_id": WORKFLOW_ID,
        "status": "completed",
        "force": bool(force),
        "outputs": {
            "storyboard_path": STORYBOARD_REL,
            "edit_storyboard_path": EDIT_REL,
            "result_path": REPORT_REL,
            "shot_count": 1,
            "scene_count": 1,
            "dialogue_count": len(items),
        },
        "warnings": [],
        "updated_at": now_iso(),
    }
    write_json(workspace / REPORT_REL, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the TalkingHead_V1 StoryBoard draft.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args()
    result = run(Path(args.workspace), force=args.force)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
