from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
GENERATE_PATH = THIS_DIR / "01_StoryBoardGenerate.py"
spec = importlib.util.spec_from_file_location("talking_head_storyboard_generate", GENERATE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot load {GENERATE_PATH}")
generate = importlib.util.module_from_spec(spec)
sys.modules.setdefault("talking_head_storyboard_generate", generate)
spec.loader.exec_module(generate)

CONFIG_PATH = THIS_DIR / "03_StoryBoardConfig.py"
config_spec = importlib.util.spec_from_file_location("talking_head_storyboard_config", CONFIG_PATH)
if config_spec is None or config_spec.loader is None:
    raise RuntimeError(f"Cannot load {CONFIG_PATH}")
storyboard_config = importlib.util.module_from_spec(config_spec)
sys.modules.setdefault("talking_head_storyboard_config", storyboard_config)
config_spec.loader.exec_module(storyboard_config)


WORKFLOW_ID = generate.WORKFLOW_ID
TOOL_NAME = "02_StoryBoardStructure"
TOOL_VERSION = "0.3.0"
REPORT_REL = "S3_02_StoryBoardStructure/Report/Result.json"
OUTPUT_REL = "S3_02_StoryBoardStructure/Output/srt_storyboard.json"
FINAL_SRT_ITEMS_REL = "SessionOutput/subtitle/final_srt_frame_items.json"
REWRITTEN_SRT_ITEMS_REL = "SessionOutput/subtitle/rewritten_srt_items.json"
REWRITTEN_SRT_REL = "SessionOutput/subtitle/rewritten_dialogue.srt"
CALIBRATION_TEXT_MIN_UNITS = 18
CALIBRATION_TEXT_MAX_UNITS = 80


def spoken_unit_count(value: str) -> int:
    text = generate.text(value)
    cjk = re.findall(r"[\u3400-\u9fff]", text)
    latin_or_digit = re.findall(r"[A-Za-z0-9]", text)
    return len(cjk) + len(latin_or_digit)


def calibration_text(items: list[dict]) -> str:
    parts: list[str] = []
    units = 0
    for item in items:
        dialogue = generate.text(item.get("dialogue") or item.get("text"))
        if not dialogue:
            continue
        parts.append(dialogue)
        units += spoken_unit_count(dialogue)
        if units >= CALIBRATION_TEXT_MAX_UNITS:
            break
    text = "。".join(parts).strip("。")
    if spoken_unit_count(text) >= CALIBRATION_TEXT_MIN_UNITS:
        return text
    return text or "你好，这是一段人物口播克隆声音语速校准测试。"


def item_estimated_duration(item: dict, seconds_per_unit: float) -> float:
    dialogue = generate.text(item.get("dialogue") or item.get("text"))
    units = max(1, spoken_unit_count(dialogue))
    return max(0.2, round(units * seconds_per_unit, 3))


def join_dialogues(parts: list[str]) -> str:
    cleaned = [generate.text(part).strip(" ，,") for part in parts if generate.text(part)]
    if not cleaned:
        return ""
    result = cleaned[0]
    for part in cleaned[1:]:
        if result[-1:] in "。！？!?；;，,":
            result += part
        else:
            result += "，" + part
    return result


def group_items_to_dialogues(items: list[dict], seconds_per_unit: float, segment_seconds: float, source: str, calibration: dict) -> tuple[list[dict], list[dict]]:
    grouped: list[dict] = []
    warnings: list[dict] = []
    current: list[dict] = []
    current_duration = 0.0

    def flush() -> None:
        nonlocal current, current_duration
        if not current:
            return
        index = len(grouped) + 1
        srt_ids = [generate.text(item.get("srt_id"), f"srt_{item.get('index') or index:04d}") for item in current]
        dialogue = join_dialogues([generate.text(item.get("dialogue") or item.get("text")) for item in current])
        start = round(sum(generate.number(item.get("duration"), 0.0) for item in grouped), 3)
        duration = round(max(0.2, current_duration), 3)
        end = round(start + duration, 3)
        grouped.append({
            "index": index,
            "dialogue_group_id": f"dialogue_{index:03d}",
            "dialogue_asset_key": f"dialogue_{index:03d}",
            "srt_id": srt_ids[0] if srt_ids else f"srt_{index:04d}",
            "srt_ids": srt_ids,
            "dialogue": dialogue,
            "start": start,
            "end": end,
            "duration": duration,
            "timing_source": source,
            "source_srt_items": current,
            "voice_timing_estimate": {
                "source_srt_count": len(current),
                "units": sum(max(1, spoken_unit_count(generate.text(item.get("dialogue") or item.get("text")))) for item in current),
                "seconds_per_unit": round(seconds_per_unit, 6),
                "single_video_length_seconds": round(segment_seconds, 3),
                "calibration_audio": calibration.get("audio_path", ""),
                "calibration_duration_seconds": calibration.get("duration_seconds", 0),
                "calibration_units": calibration.get("units", 0),
            },
        })
        current = []
        current_duration = 0.0

    for raw_index, item in enumerate(items, start=1):
        duration = generate.number(item.get("estimated_duration"), 0.0)
        if duration <= 0:
            duration = item_estimated_duration(item, seconds_per_unit)
        units = max(1, spoken_unit_count(generate.text(item.get("dialogue") or item.get("text"))))
        timed_item = {
            **item,
            "index": int(item.get("index") or raw_index),
            "estimated_duration": duration,
            "voice_timing_estimate": {
                "units": units,
                "seconds_per_unit": round(seconds_per_unit, 6),
                "duration": duration,
            },
        }
        if duration > segment_seconds:
            warnings.append({
                "code": "single_srt_estimated_duration_over_single_video_length",
                "srt_id": generate.text(item.get("srt_id"), f"srt_{raw_index:04d}"),
                "estimated_duration": duration,
                "single_video_length_seconds": round(segment_seconds, 3),
                "message": "单句估算时长超过单个视频长度；人物口播不拆句，只提示。",
            })
        if current and current_duration + duration > segment_seconds:
            flush()
        current.append(timed_item)
        current_duration += duration
    flush()
    return grouped, warnings


def retime_items(items: list[dict], seconds_per_unit: float, source: str, calibration: dict) -> list[dict]:
    cursor = 0.0
    timed: list[dict] = []
    for index, item in enumerate(items, start=1):
        dialogue = generate.text(item.get("dialogue") or item.get("text"))
        units = max(1, spoken_unit_count(dialogue))
        duration = max(0.2, round(units * seconds_per_unit, 3))
        start = round(cursor, 3)
        end = round(cursor + duration, 3)
        timed.append({
            **item,
            "index": int(item.get("index") or index),
            "start": start,
            "end": end,
            "duration": duration,
            "estimated_duration": duration,
            "timing_source": source,
            "voice_timing_estimate": {
                "units": units,
                "seconds_per_unit": round(seconds_per_unit, 6),
                "calibration_audio": calibration.get("audio_path", ""),
                "calibration_duration_seconds": calibration.get("duration_seconds", 0),
                "calibration_units": calibration.get("units", 0),
            },
        })
        cursor = end
    return timed


def retime_payload(payload: dict, timed_items: list[dict], calibration: dict) -> dict:
    """Overlay calibrated timing on a subtitle payload without replacing its text."""
    result = dict(payload) if isinstance(payload, dict) else {}
    source_items = [item for item in generate.list_value(result.get("items")) if isinstance(item, dict)]
    by_id = {
        generate.text(item.get("srt_id") or item.get("id")): item
        for item in timed_items
        if generate.text(item.get("srt_id") or item.get("id"))
    }
    timing_source = f"{generate.text(calibration.get('provider'), 'heygen')}_voice_calibration"
    updated: list[dict] = []
    for index, source_item in enumerate(source_items):
        srt_id = generate.text(source_item.get("srt_id") or source_item.get("id"))
        timing = by_id.get(srt_id) or (timed_items[index] if index < len(timed_items) else {})
        if not timing:
            updated.append(dict(source_item))
            continue
        updated.append({
            **source_item,
            "start": generate.number(timing.get("start")),
            "end": generate.number(timing.get("end")),
            "duration": generate.number(timing.get("duration")),
            "estimated_duration": generate.number(timing.get("estimated_duration"), generate.number(timing.get("duration"))),
            "timing_source": generate.text(timing.get("timing_source"), timing_source),
            "voice_timing_estimate": generate.dict_value(timing.get("voice_timing_estimate")),
        })
    result["items"] = updated
    result["timing_source"] = timing_source
    result["voice_calibration"] = calibration
    result["updated_at"] = generate.now_iso()
    return result


def srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def build_retimed_srt(items: list[dict]) -> str:
    blocks: list[str] = []
    for index, item in enumerate(items, start=1):
        dialogue = generate.text(item.get("dialogue") or item.get("text"))
        if not dialogue:
            continue
        blocks.append(
            f"{index}\n{srt_timestamp(generate.number(item.get('start')))} --> "
            f"{srt_timestamp(generate.number(item.get('end')))}\n{dialogue}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def persist_retimed_subtitles(workspace: Path, timed_items: list[dict], calibration: dict) -> list[str]:
    """Persist calibrated per-line timing for the UI and later workflow stages."""
    written: list[str] = []
    for rel_path in (FINAL_SRT_ITEMS_REL, REWRITTEN_SRT_ITEMS_REL):
        path = workspace / rel_path
        payload = generate.read_json(path, {}) or {}
        if not isinstance(payload, dict) or not generate.list_value(payload.get("items")):
            continue
        generate.write_json(path, retime_payload(payload, timed_items, calibration))
        written.append(rel_path)
    rewritten_payload = generate.read_json(workspace / REWRITTEN_SRT_ITEMS_REL, {}) or {}
    rewritten_items = [item for item in generate.list_value(rewritten_payload.get("items")) if isinstance(item, dict)]
    if rewritten_items:
        srt_path = workspace / REWRITTEN_SRT_REL
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text(build_retimed_srt(rewritten_items), encoding="utf-8")
        written.append(REWRITTEN_SRT_REL)
    return written


def apply_voice_calibration(workspace: Path, items: list[dict], meta: dict, variables: dict, force: bool) -> tuple[list[dict], dict, list[dict]]:
    warnings: list[dict] = []
    voice = storyboard_config.voice_config(meta, variables)
    segment_seconds = storyboard_config.target_segment_seconds(meta, variables)
    if voice.get("status") != "selected":
        warnings.append({"code": "voice_not_selected", "message": "未选择克隆声音，02 保留现有对白时间。"})
        timed = []
        for item in items:
            duration = generate.number(item.get("duration"), segment_seconds)
            units = max(1, spoken_unit_count(generate.text(item.get("dialogue") or item.get("text"))))
            timed.append({**item, "duration": duration, "estimated_duration": duration, "voice_timing_estimate": {"units": units, "duration": duration}})
        grouped, grouping_warnings = group_items_to_dialogues(timed, 1.0, segment_seconds, "existing_srt_duration", {"status": "skipped"})
        return grouped, {"status": "skipped", "reason": "voice_not_selected"}, warnings + grouping_warnings
    text = calibration_text(items)
    units = max(1, spoken_unit_count(text))
    output_path = workspace / "SessionOutput/storyboard/Working/talking_head_voice_calibration.wav"
    audio_meta = storyboard_config.generate_clone_audio(voice["provider"], text, voice["voice_id"], voice["tempo"], output_path, force=force, voice_runtime_config=voice.get("clone_config"))
    duration = generate.number(audio_meta.get("duration_seconds"), 0.0)
    if duration <= 0:
        warnings.append({"code": "voice_calibration_duration_invalid", "message": "克隆声音校准音频时长无效，02 保留现有对白时间。"})
        return items, {"status": "failed", "reason": "duration_invalid"}, warnings
    seconds_per_unit = duration / units
    calibration = {
        "status": "completed",
        "provider": voice.get("provider"),
        "voice_id": voice.get("voice_id"),
        "voice_label": voice.get("voice_label"),
        "tempo": voice.get("tempo"),
        "sample_text": text,
        "units": units,
        "duration_seconds": round(duration, 3),
        "seconds_per_unit": round(seconds_per_unit, 6),
        "audio_path": "SessionOutput/storyboard/Working/talking_head_voice_calibration.wav",
        "cache_hit": bool(audio_meta.get("cache_hit")),
    }
    timing_source = f"{voice['provider']}_voice_calibration"
    timed_items = retime_items(items, seconds_per_unit, timing_source, calibration)
    grouped, grouping_warnings = group_items_to_dialogues(timed_items, seconds_per_unit, segment_seconds, timing_source, calibration)
    calibration["retimed_item_count"] = len(timed_items)
    calibration["retimed_total_duration_seconds"] = round(sum(generate.number(item.get("duration")) for item in timed_items), 3)
    return grouped, calibration, warnings + grouping_warnings


def flatten_source_dialogues(storyboard: dict) -> list[dict]:
    dialogues: list[dict] = []
    for shot in generate.list_value(storyboard.get("shots")):
        if not isinstance(shot, dict):
            continue
        for scene in generate.list_value(shot.get("scenes")):
            if not isinstance(scene, dict):
                continue
            for dialogue in generate.list_value(scene.get("dialogue_items") or scene.get("dialogues")):
                if isinstance(dialogue, dict):
                    dialogues.append(dict(dialogue))
    return sorted(dialogues, key=lambda item: (generate.number(item.get("start")), generate.number(item.get("end"))))


def run(workspace: Path, force: bool = False) -> dict:
    workspace = workspace.resolve()
    variables = generate.read_json(workspace / generate.VARIABLES_REL, {}) or {}
    if not isinstance(variables, dict) or generate.text(variables.get("workflow_id")) != WORKFLOW_ID:
        raise RuntimeError("请先运行 TalkingHead_V1/00 生成当前人物口播 Variables。")
    items = generate.rewritten_items(workspace)
    if not items:
        source = generate.read_json(workspace / generate.STORYBOARD_REL, {}) or {}
        dialogues = flatten_source_dialogues(source)
        items = []
        for index, dialogue in enumerate(dialogues, start=1):
            items.append({
                "index": index,
                "srt_id": generate.text(dialogue.get("srt_id"), f"srt_{index:04d}"),
                "dialogue": generate.text(dialogue.get("dialogue") or dialogue.get("text")),
                "start": generate.number(dialogue.get("start")),
                "end": generate.number(dialogue.get("end")),
                "duration": generate.number(dialogue.get("duration"), generate.number(dialogue.get("end")) - generate.number(dialogue.get("start"))),
            })
    if not items:
        raise RuntimeError("故事版分镜生成需要口播台词，请先运行 04_01 和 01。")
    items, calibration, warnings = apply_voice_calibration(workspace, items, {}, variables, force)
    source_srt_items = [
        source_item
        for item in items
        for source_item in generate.list_value(item.get("source_srt_items"))
        if isinstance(source_item, dict)
    ]
    retimed_paths: list[str] = []
    if calibration.get("status") == "completed" and source_srt_items:
        retimed_paths = persist_retimed_subtitles(workspace, source_srt_items, calibration)
    structured = generate.source_storyboard(workspace, items, variables)
    task_snapshot = generate.dict_value(variables.get("task"))
    task_id = int(task_snapshot.get("task_id") or variables.get("task_id") or 0)
    session_id = int(task_snapshot.get("session_id") or variables.get("session_id") or 0)
    edit = generate.edit_storyboard(structured, task_id=task_id, session_id=session_id)
    generate.write_json(workspace / generate.STORYBOARD_REL, structured)
    generate.write_json(workspace / generate.EDIT_REL, edit)
    generate.write_json(workspace / OUTPUT_REL, structured)
    if isinstance(variables, dict):
        variables.update({
            "workflow_id": WORKFLOW_ID,
            "talking_head_storyboard_structured": True,
            "talking_head_dialogue_count": len(items),
            "talking_head_voice_calibration": calibration,
            "updated_at": generate.now_iso(),
        })
        generate.write_json(workspace / generate.VARIABLES_REL, variables)
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workflow_id": WORKFLOW_ID,
        "status": "completed",
        "force": bool(force),
        "outputs": {
            "storyboard_path": generate.STORYBOARD_REL,
            "edit_storyboard_path": generate.EDIT_REL,
            "result_path": REPORT_REL,
            "shot_count": 1,
            "scene_count": 1,
            "dialogue_count": len(items),
            "voice_calibration": calibration,
            "retimed_srt_items": len(source_srt_items) if retimed_paths else 0,
            "retimed_srt_paths": retimed_paths,
        },
        "warnings": warnings,
        "updated_at": generate.now_iso(),
    }
    generate.write_json(workspace / REPORT_REL, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize TalkingHead_V1 to one Shot and one Scene, merging SRT lines into Dialogue segments by single video length.")
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
