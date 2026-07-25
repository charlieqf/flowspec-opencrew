from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any


TOOL_NAME = "RebuildSceneMarkBuilder"
TOOL_VERSION = "0.1.0"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
MODES = ("single", "first_last")
MARK_MODES = ("rebuild", "refresh_prompts")
VALIDATE_MODES = ("none", "quick", "full")


def load_rebuild_03() -> Any:
    module_path = Path(__file__).with_name("02_Rebuild_ShotPlanBuilder.py")
    spec = importlib.util.spec_from_file_location("rebuild_shot_plan_builder", module_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load rebuild 03 module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REBUILD_03 = load_rebuild_03()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I).strip()
        stripped = re.sub(r"\s*```$", "", stripped).strip()
    start = stripped.find("{")
    if start < 0:
        raise RuntimeError("Model response did not contain a JSON object")
    decoder = json.JSONDecoder()
    parsed, _ = decoder.raw_decode(stripped[start:])
    if not isinstance(parsed, dict):
        raise RuntimeError("Model response JSON must be an object")
    return parsed


def message_role(message: dict[str, Any]) -> str:
    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    return str(info.get("role") or message.get("role") or "")


def message_id(message: dict[str, Any]) -> str:
    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    return str(info.get("id") or message.get("id") or "")


def message_parent_id(message: dict[str, Any]) -> str:
    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    return str(info.get("parentID") or message.get("parentID") or "")


def message_created_at(message: dict[str, Any]) -> int:
    info = message.get("info") if isinstance(message.get("info"), dict) else {}
    time_info = info.get("time") if isinstance(info.get("time"), dict) else {}
    return int((time_info.get("created") or message.get("createdAt") or 0) or 0)


def message_text(message: dict[str, Any]) -> str:
    return "\n".join(str(part.get("text") or "").strip() for part in (message.get("parts") or []) if isinstance(part, dict) and part.get("type") == "text").strip()


def matching_user_prompt_id(messages: list[dict[str, Any]], started_at: int, prompt_text: str) -> str:
    expected = prompt_text.strip()
    for message in reversed(messages):
        if message_role(message) != "user":
            continue
        if message_created_at(message) < started_at:
            continue
        text = message_text(message)
        if text == expected or (expected and text.startswith(expected[:2000])):
            return message_id(message)
    return ""


def assistant_text_for_parent(messages: list[dict[str, Any]], parent_id: str) -> str:
    if not parent_id:
        return ""
    for message in reversed(messages):
        if message_role(message) != "assistant":
            continue
        if message_parent_id(message) != parent_id:
            continue
        text = message_text(message)
        if text:
            return text
    return ""


def image_file_part(path: Path, workspace: Path) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    try:
        filename = path.relative_to(workspace).as_posix()
    except ValueError:
        filename = path.name
    return {"type": "file", "mime": mime, "filename": filename, "url": f"data:{mime};base64,{encoded}"}


def resolve_workspace_path(workspace: Path, path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else workspace / path


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def field_text(value: Any, keys: tuple[str, ...]) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        return " / ".join(str(item).strip() for item in value.values() if isinstance(item, str) and item.strip())
    return ""


def parse_frame_name(frame: dict[str, Any]) -> dict[str, Any]:
    path = str(frame.get("path") or "")
    name = Path(path).name
    match = re.search(r"pyscenedetect_(start|middle|end_near)_(\d+)_t([0-9.]+)\.jpg", name)
    if not match:
        return {"role_hint": str(frame.get("role") or ""), "frame_order": None}
    return {"role_hint": match.group(1), "frame_order": int(match.group(2)), "time_hint": safe_float(match.group(3))}


def compact_keyframes(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    for frame in sorted(frames, key=lambda item: (safe_float(item.get("time"), 1_000_000.0), str(item.get("path") or ""))):
        path = str(frame.get("path") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        parsed = parse_frame_name(frame)
        rows.append({
            "time": round(safe_float(frame.get("time")), 3),
            "path": path,
            "source": frame.get("source") or "",
            "role_hint": parsed.get("role_hint") or frame.get("role") or "",
            "frame_order": parsed.get("frame_order"),
        })
    return rows


def merge_keyframes(*groups: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, list):
            continue
        for frame in group:
            if not isinstance(frame, dict):
                continue
            path = str(frame.get("path") or "").strip()
            if not path:
                continue
            current = merged.get(path) or {}
            merged[path] = {**dict(frame), **current}
    return sorted(merged.values(), key=lambda item: (safe_float(item.get("time"), 1_000_000.0), str(item.get("path") or "")))


def scene_detect_keyframes(frames: Any) -> list[dict[str, Any]]:
    if not isinstance(frames, list):
        return []
    rows = []
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        source = str(frame.get("source") or "")
        path = str(frame.get("path") or "")
        if source == "pyscenedetect" or path.startswith("keyframes/pyscenedetect_scenes/"):
            rows.append(frame)
    return rows


def restore_shot_keyframes_from_source_package(shot: dict[str, Any], source_package: dict[str, Any]) -> None:
    segments = [item for item in (source_package.get("segments") or []) if isinstance(item, dict)]
    source_segment_id = str(shot.get("source_segment_id") or "")
    source_index = int(safe_float(shot.get("source_index"), 0.0))
    segment = next((item for item in segments if str(item.get("segment_id") or "") == source_segment_id), None)
    if segment is None and source_index:
        segment = next((item for item in segments if int(safe_float(item.get("index"), 0.0)) == source_index), None)
    if not segment:
        return
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    reference["keyframes"] = merge_keyframes(scene_detect_keyframes(segment.get("keyframes")), scene_detect_keyframes(reference.get("keyframes")))
    shot["reference"] = reference


def source_segment_for_shot(shot: dict[str, Any], source_package: dict[str, Any]) -> dict[str, Any]:
    segments = [item for item in (source_package.get("segments") or []) if isinstance(item, dict)]
    source_segment_id = str(shot.get("source_segment_id") or "")
    source_index = int(safe_float(shot.get("source_index"), 0.0))
    segment = next((item for item in segments if str(item.get("segment_id") or "") == source_segment_id), None)
    if segment is None and source_index:
        segment = next((item for item in segments if int(safe_float(item.get("index"), 0.0)) == source_index), None)
    return segment if isinstance(segment, dict) else {}


def attach_source_ocr_to_shot(shot: dict[str, Any], source_package: dict[str, Any]) -> None:
    segment = source_segment_for_shot(shot, source_package)
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    if isinstance(segment.get("ocr_text"), list):
        reference["ocr_text"] = segment.get("ocr_text") or []
    shot["reference"] = reference


def srt_seconds(value: str) -> float:
    match = re.match(r"(\d+):(\d+):(\d+),(\d+)", value.strip())
    if not match:
        return 0.0
    hours, minutes, seconds, millis = [int(item) for item in match.groups()]
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def parse_srt_blocks(srt_text: str) -> list[dict[str, Any]]:
    blocks = []
    for raw in re.split(r"\n\s*\n", str(srt_text or "").strip()):
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        time_line = next((line for line in lines if "-->" in line), "")
        if not time_line:
            continue
        start_text, end_text = [part.strip() for part in time_line.split("-->", 1)]
        text_lines = [line for line in lines if line != time_line and not line.isdigit()]
        text = " ".join(text_lines).strip()
        if text:
            blocks.append({"start": srt_seconds(start_text), "end": srt_seconds(end_text), "text": text})
    return blocks


def srt_for_range(srt_text: str, shot_start: float, start: float, end: float) -> str:
    blocks = parse_srt_blocks(srt_text)
    if not blocks:
        return str(srt_text or "").strip()
    local_start = max(0.0, start - shot_start)
    local_end = max(local_start, end - shot_start)
    rows = [str(block.get("text") or "") for block in blocks if min(local_end, safe_float(block.get("end"))) - max(local_start, safe_float(block.get("start"))) > 0]
    if rows:
        return " ".join(rows).strip()
    expanded_start = max(0.0, local_start - 1.5)
    expanded_end = local_end + 1.5
    rows = [str(block.get("text") or "") for block in blocks if min(expanded_end, safe_float(block.get("end"))) - max(expanded_start, safe_float(block.get("start"))) > 0]
    if rows:
        return " ".join(rows).strip()
    nearest = min(blocks, key=lambda block: min(abs(local_start - safe_float(block.get("end"))), abs(local_end - safe_float(block.get("start")))))
    gap = min(abs(local_start - safe_float(nearest.get("end"))), abs(local_end - safe_float(nearest.get("start"))))
    return str(nearest.get("text") or "").strip() if gap <= 3.0 else ""


def srt_candidates_for_range(srt_text: str, shot_start: float, start: float, end: float) -> list[dict[str, Any]]:
    blocks = parse_srt_blocks(srt_text)
    if not blocks:
        return []
    local_start = max(0.0, start - shot_start)
    local_end = max(local_start, end - shot_start)
    expanded_start = max(0.0, local_start - 2.0)
    expanded_end = local_end + 2.0
    rows = []
    for index, block in enumerate(blocks, start=1):
        block_start = safe_float(block.get("start"))
        block_end = safe_float(block.get("end"))
        overlap = max(0.0, min(local_end, block_end) - max(local_start, block_start))
        expanded_overlap = max(0.0, min(expanded_end, block_end) - max(expanded_start, block_start))
        if overlap <= 0 and expanded_overlap <= 0:
            continue
        if block_end <= local_start:
            relation = "before"
        elif block_start >= local_end:
            relation = "after"
        else:
            relation = "overlap"
        rows.append({
            "index": index,
            "relation": relation,
            "start": round(block_start, 3),
            "end": round(block_end, 3),
            "scene_local_start": round(local_start, 3),
            "scene_local_end": round(local_end, 3),
            "overlap_seconds": round(overlap, 3),
            "text": str(block.get("text") or "").strip(),
        })
    if rows:
        return rows
    nearest = min(blocks, key=lambda block: min(abs(local_start - safe_float(block.get("end"))), abs(local_end - safe_float(block.get("start")))))
    return [{
        "index": blocks.index(nearest) + 1,
        "relation": "nearest",
        "start": round(safe_float(nearest.get("start")), 3),
        "end": round(safe_float(nearest.get("end")), 3),
        "scene_local_start": round(local_start, 3),
        "scene_local_end": round(local_end, 3),
        "overlap_seconds": 0,
        "text": str(nearest.get("text") or "").strip(),
    }]


def compact_ocr_text(text: Any) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\b[A-Za-z0-9]{4,}\b", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def ocr_candidates_for_range(ocr_text: Any, start: float, end: float, first_path: str = "", last_path: str = "") -> list[dict[str, Any]]:
    if not isinstance(ocr_text, list):
        return []
    expanded_start = start - 2.0
    expanded_end = end + 2.0
    rows = []
    for index, item in enumerate(ocr_text, start=1):
        if not isinstance(item, dict):
            continue
        text = compact_ocr_text(item.get("text"))
        if not text:
            continue
        item_start = safe_float(item.get("start"))
        item_end = safe_float(item.get("end"), item_start)
        paths = [str(path or "") for path in (item.get("source_keyframe_paths") or [])]
        path_match = bool((first_path and first_path in paths) or (last_path and last_path in paths))
        overlap = max(0.0, min(end, item_end) - max(start, item_start))
        nearby_overlap = max(0.0, min(expanded_end, item_end) - max(expanded_start, item_start))
        if not path_match and overlap <= 0 and nearby_overlap <= 0:
            continue
        if path_match:
            relation = "keyframe_match"
        elif overlap > 0:
            relation = "overlap"
        elif item_end <= start:
            relation = "before"
        else:
            relation = "after"
        rows.append({
            "index": index,
            "relation": relation,
            "start": round(item_start, 3),
            "end": round(item_end, 3),
            "overlap_seconds": round(overlap, 3),
            "confidence": item.get("confidence"),
            "text": text,
            "raw_text": str(item.get("text") or "").strip(),
            "source_keyframe_paths": paths[:8],
            "text_candidates": [
                {"time": candidate.get("time"), "text": compact_ocr_text(candidate.get("text")), "confidence": candidate.get("confidence"), "path": candidate.get("path")}
                for candidate in (item.get("text_candidates") or [])[:8]
                if isinstance(candidate, dict) and compact_ocr_text(candidate.get("text"))
            ],
        })
    return sorted(rows, key=lambda row: ({"keyframe_match": 0, "overlap": 1, "before": 2, "after": 3}.get(str(row.get("relation")), 4), -safe_float(row.get("overlap_seconds")), safe_float(row.get("start"))))[:8]


def build_prompt(context: dict[str, Any], shot: dict[str, Any], keyframes: list[dict[str, Any]], mode: str) -> str:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    payload = {
        "task": {"task_id": context["task_id"], "session_id": context["session_id"], "analysis_task_id": context["analysis_task_id"]},
        "final_prompt": context["final_prompt"],
        "mode": mode,
        "shot": {
            "shot_id": shot.get("shot_id"),
            "source_segment_id": shot.get("source_segment_id"),
            "start": shot.get("start"),
            "end": shot.get("end"),
            "duration": shot.get("duration"),
            "role": shot.get("role"),
            "formula_slot": shot.get("formula_slot"),
            "srt_text": reference.get("srt_text") or "",
            "ocr_text": reference.get("ocr_text") or [],
            "ui_summary": field_text(shot.get("ui_summary"), ("summary", "what_happens", "title")),
            "rebuild_direction": field_text(shot.get("rebuild_direction"), ("direction", "new_scene", "new_spoken_script")),
            "generation_hint": field_text(shot.get("generation_hint"), ("hint", "visual", "prompt", "motion")),
            "quality_notes": shot.get("quality_notes") or [],
        },
        "keyframes": keyframes,
    }
    return """你是 OpenClip Rebuild Scene Mark Builder。

你需要先阅读随消息附带的 keyframe 图片，再结合一个 shot 内的 keyframe 时间、字幕 SRT、shot summary、rebuild direction 和 generation hint，把该 shot 切成若干适合 VEO / Sora / Grok / Wan 生成的小 Scene，并返回每个 Scene 的关键帧标记和视频生成描述。

核心优先级：
1. 视频提示词必须优先来自 keyframe 图片本身的视觉理解，包括主体、动作、构图、道具、场景、光线、镜头距离和画面变化。
2. Scene 边界必须优先按图片之间的视觉连续性判断，而不是按字幕语义、流程语义或主题相似性合并。
3. 相邻 keyframe 如果出现主体、场景、构图、镜头距离、动作阶段或主要道具的明显变化，必须切成不同 Scene。
4. SRT、ui_summary、rebuild_direction、generation_hint 只能作为语义匹配和补充，不能覆盖图片中不存在的主要画面，也不能把视觉上不连续的图片合并成同一 Scene。
5. 如果字幕语义和图片不一致，以图片为准，并在 summary / visual_change 中自然说明画面实际内容。
6. 如果图片上有 OCR 字幕，必须优先用 shot.ocr_text 定位该 Scene 对应的旁白，再和 shot.srt_text 比对，最终输出干净、准确、短的 srt_text。
7. 包装文字、品牌、水印、账号、乱码、规格参数属于 visual text，不要当作旁白 srt_text；只能作为画面信息参考。
8. 每个 Scene 的 srt_text 必须是与该 Scene 首/尾帧画面最匹配的关键文字片段，可以是半句话、短语或局部语义片段；不要直接复制整段 SRT block，也不要包含与画面不对应的前后语义。
9. video_prompt 应描述“用这些图片能生成什么连续画面”，而不是复述字幕文案。
10. 你会收到每张图片作为 file part，filename 与输入 keyframes.path 对应；选择 keyframe_path 时必须使用输入 JSON 中的 path。

严格要求：
1. 只输出 JSON 对象，不要解释，不要 Markdown。
2. 只能从输入 keyframes 中选择 keyframe_path，不得新增不存在的路径。
3. mode=single 时，每个 scene 只选择 single_keyframe_path。
4. mode=first_last 时，每个 scene 必须选择不同的 first_keyframe_path 和 last_keyframe_path；如果只能用一张，不要输出该 scene。
5. first_keyframe_path 和 last_keyframe_path 必须属于同一个视觉连续 Scene，不能跨越明显切镜或视觉断点。
6. 同一张 keyframe 只能属于一个 Scene，不能同时作为前一个 Scene 的尾帧和后一个 Scene 的首帧。
7. scene start/end 必须在 shot start/end 范围内，duration=end-start。
8. srt_text 必须是与画面最相关的短片段，srt_match_reason 简要说明为什么选这一段；如果没有直接对应，选择最接近短片段并说明。
9. srt_match_source 必须为 ocr_aligned、srt_only、nearest_srt、visual_text_rejected 或 fallback 之一；如果使用了 OCR 定位，填写 ocr_text_used。
10. scene 描述必须以图片画面和画面变化为主，结合 shot 字幕和重建方向，适合作为视频模型提示词。
11. base video prompt 不要要求模型生成字幕、logo、水印或屏幕文字；字幕和标题后续由 overlay 处理。
12. 健康养生内容不得医疗化、不得承诺疗效、不得夸大。

输出 JSON 结构：
{
  "shot_id": "shot_002",
  "mode": "first_last",
  "scenes": [
    {
      "scene_mark_id": "shot_002_scene_001",
      "start": 0.7,
      "end": 1.3,
      "duration": 0.6,
      "single_keyframe_path": "",
      "first_keyframe_path": "keyframes/...jpg",
      "last_keyframe_path": "keyframes/...jpg",
      "srt_text": "",
      "srt_match_reason": "",
      "srt_match_source": "ocr_aligned",
      "ocr_text_used": "",
      "summary": "",
      "visual_change": "",
      "motion_prompt": "",
      "video_prompt": "",
      "negative_prompt": "watermark, logo, subtitles, unreadable text, distorted face, bad hands, low quality",
      "model_notes": {"veo": "", "sora": "", "grok": "", "wan": ""},
      "warnings": []
    }
  ],
  "validation": {"status": "passed", "warnings": []}
}

输入上下文：
""" + json.dumps(payload, ensure_ascii=False, indent=2)


def build_refresh_prompt(context: dict[str, Any], shot: dict[str, Any], keyframes: list[dict[str, Any]], scene_marks: list[dict[str, Any]]) -> str:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    shot_start = safe_float(shot.get("start"))
    srt_text = str(reference.get("srt_text") or "")
    ocr_text = reference.get("ocr_text") if isinstance(reference.get("ocr_text"), list) else []
    fixed_scene_marks = []
    for mark in scene_marks:
        if not isinstance(mark, dict):
            continue
        item = dict(mark)
        start = safe_float(mark.get("start"))
        end = safe_float(mark.get("end"), start)
        keyframes_info = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
        item["srt_candidates"] = srt_candidates_for_range(srt_text, shot_start, start, end)
        item["ocr_candidates"] = ocr_candidates_for_range(ocr_text, start, end, str(keyframes_info.get("first") or keyframes_info.get("single") or ""), str(keyframes_info.get("last") or keyframes_info.get("single") or ""))
        fixed_scene_marks.append(item)
    payload = {
        "task": {"task_id": context["task_id"], "session_id": context["session_id"], "analysis_task_id": context["analysis_task_id"]},
        "final_prompt": context["final_prompt"],
        "shot": {
            "shot_id": shot.get("shot_id"),
            "source_segment_id": shot.get("source_segment_id"),
            "start": shot.get("start"),
            "end": shot.get("end"),
            "duration": shot.get("duration"),
            "role": shot.get("role"),
            "formula_slot": shot.get("formula_slot"),
            "srt_text": reference.get("srt_text") or "",
            "ocr_text_available": bool(ocr_text),
            "ui_summary": field_text(shot.get("ui_summary"), ("summary", "what_happens", "title")),
            "rebuild_direction": field_text(shot.get("rebuild_direction"), ("direction", "new_scene", "new_spoken_script")),
            "generation_hint": field_text(shot.get("generation_hint"), ("hint", "visual", "prompt", "motion")),
        },
        "fixed_scene_marks": fixed_scene_marks,
        "keyframes": keyframes,
    }
    return """你是 OpenClip Rebuild Scene Prompt Refresher。

你需要先阅读随消息附带的首/尾帧图片，然后只刷新每个既有 Scene 的视频生成提示词。Scene 边界和首尾帧已经由用户或上游工具确定，严禁修改。

核心优先级：
1. 提示词必须优先来自首/尾帧图片本身的视觉理解，包括主体、动作、构图、道具、场景、光线、镜头距离和画面变化。
2. SRT、ui_summary、rebuild_direction、generation_hint 只能作为语义匹配和补充，不能覆盖图片中不存在的主要画面。
3. 如果字幕语义和图片不一致，以图片为准。
4. 每个 Scene 会提供 srt_candidates 和 ocr_candidates。若 ocr_candidates 中存在画面字幕，必须优先用 OCR 定位当前 Scene 对应旁白，再和 srt_candidates / shot.srt_text 比对，输出干净短 srt_text。
5. 包装文字、品牌、水印、账号、乱码、规格参数属于 visual text，不要当作旁白 srt_text；如果拒绝这类 OCR，srt_match_source 用 visual_text_rejected。
6. srt_text 可以是半句话、短语或前后候选中的一小段，不要求保留完整 SRT block；不要把与当前画面不对应的后半句或下一语义段塞进来。
7. 如果候选字幕没有直接对应画面，选择最接近的一小段，并在 srt_match_reason 中说明。
8. 不得返回新的 keyframe path、start、end 或 duration。

严格要求：
1. 只输出 JSON 对象，不要解释，不要 Markdown。
2. scenes 数量和 scene_mark_id 必须与 fixed_scene_marks 一致。
3. 返回 scene_mark_id、srt_text、srt_match_reason、srt_match_source、ocr_text_used 和 scene_description 相关字段。
4. srt_text 应尽量短，只保留与画面最相关的核心片段。
5. base video prompt 不要要求模型生成字幕、logo、水印或屏幕文字；字幕和标题后续由 overlay 处理。
6. 健康养生内容不得医疗化、不得承诺疗效、不得夸大。

输出 JSON 结构：
{
  "shot_id": "shot_002",
  "scenes": [
    {
      "scene_mark_id": "shot_002_scene_001",
      "srt_text": "",
      "srt_match_reason": "",
      "srt_match_source": "ocr_aligned",
      "ocr_text_used": "",
      "summary": "",
      "visual_change": "",
      "motion_prompt": "",
      "video_prompt": "",
      "negative_prompt": "watermark, logo, subtitles, unreadable text, distorted face, bad hands, low quality",
      "model_notes": {"veo": "", "sora": "", "grok": "", "wan": ""},
      "warnings": []
    }
  ],
  "validation": {"status": "passed", "warnings": []}
}

输入上下文：
""" + json.dumps(payload, ensure_ascii=False, indent=2)


def call_model(context: dict[str, Any], shot: dict[str, Any], keyframes: list[dict[str, Any]], mode: str, image_workspace: Path, timeout_seconds: int) -> dict[str, Any]:
    started_at = int(time.time() * 1000)
    image_parts = []
    missing_images = []
    for frame in keyframes:
        path_value = str(frame.get("path") or "")
        if not path_value:
            continue
        image_path = resolve_workspace_path(image_workspace, path_value)
        if image_path.exists() and image_path.is_file():
            image_parts.append(image_file_part(image_path, image_workspace))
        else:
            missing_images.append(path_value)
    if not image_parts:
        raise RuntimeError(f"No readable keyframe images for {shot.get('shot_id')}: {missing_images}")
    prompt_text = build_prompt(context, shot, keyframes, mode)
    REBUILD_03.request_json(
        context,
        "POST",
        f"/session/{context['opencode_session_id']}/prompt_async",
        {
            "parts": [{"type": "text", "text": prompt_text}] + image_parts,
            "model": {"providerID": context["run_model_provider"], "modelID": context["run_model_id"]},
        },
        query={"directory": context["workspace_dir"]},
        timeout=30,
    )
    deadline = time.time() + timeout_seconds
    response_text = ""
    parent_id = ""
    while time.time() < deadline:
        messages = REBUILD_03.request_json(context, "GET", f"/session/{context['opencode_session_id']}/message", None, query={"directory": context["workspace_dir"], "limit": "160"}, timeout=30) or []
        parent_id = parent_id or matching_user_prompt_id(messages, started_at, prompt_text)
        response_text = assistant_text_for_parent(messages, parent_id)
        if response_text:
            break
        time.sleep(1)
    if not response_text:
        raise RuntimeError(f"OpenCode timed out before returning scene marks for {shot.get('shot_id')}")
    return extract_json_object(response_text)


def call_refresh_model(context: dict[str, Any], shot: dict[str, Any], keyframes: list[dict[str, Any]], scene_marks: list[dict[str, Any]], image_workspace: Path, timeout_seconds: int) -> dict[str, Any]:
    started_at = int(time.time() * 1000)
    image_parts = []
    missing_images = []
    for frame in keyframes:
        path_value = str(frame.get("path") or "")
        if not path_value:
            continue
        image_path = resolve_workspace_path(image_workspace, path_value)
        if image_path.exists() and image_path.is_file():
            image_parts.append(image_file_part(image_path, image_workspace))
        else:
            missing_images.append(path_value)
    if not image_parts:
        raise RuntimeError(f"No readable scene mark images for {shot.get('shot_id')}: {missing_images}")
    prompt_text = build_refresh_prompt(context, shot, keyframes, scene_marks)
    REBUILD_03.request_json(
        context,
        "POST",
        f"/session/{context['opencode_session_id']}/prompt_async",
        {
            "parts": [{"type": "text", "text": prompt_text}] + image_parts,
            "model": {"providerID": context["run_model_provider"], "modelID": context["run_model_id"]},
        },
        query={"directory": context["workspace_dir"]},
        timeout=30,
    )
    deadline = time.time() + timeout_seconds
    response_text = ""
    parent_id = ""
    while time.time() < deadline:
        messages = REBUILD_03.request_json(context, "GET", f"/session/{context['opencode_session_id']}/message", None, query={"directory": context["workspace_dir"], "limit": "160"}, timeout=30) or []
        parent_id = parent_id or matching_user_prompt_id(messages, started_at, prompt_text)
        response_text = assistant_text_for_parent(messages, parent_id)
        if response_text:
            break
        time.sleep(1)
    if not response_text:
        raise RuntimeError(f"OpenCode timed out before refreshing scene prompts for {shot.get('shot_id')}")
    return extract_json_object(response_text)


def frame_by_path(keyframes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(frame.get("path") or ""): frame for frame in keyframes if isinstance(frame, dict) and str(frame.get("path") or "")}


def normalized_scene_path(scene: dict[str, Any], key: str) -> str:
    return str(scene.get(key) or "").strip()


def normalize_model_scenes(shot: dict[str, Any], model_payload: dict[str, Any], original_keyframes: list[dict[str, Any]], mode: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    by_path = frame_by_path(original_keyframes)
    shot_id = str(shot.get("shot_id") or "shot")
    shot_start = safe_float(shot.get("start"))
    shot_end = safe_float(shot.get("end"), shot_start)
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    srt_text = str(reference.get("srt_text") or "")
    warnings: list[str] = []
    kept_by_path: dict[str, dict[str, Any]] = {path: dict(frame) for path, frame in by_path.items()}
    for frame in kept_by_path.values():
        frame.pop("scene_mark", None)
    scene_marks: list[dict[str, Any]] = []
    used_keyframe_paths: set[str] = set()
    scenes = model_payload.get("scenes") if isinstance(model_payload.get("scenes"), list) else []
    for index, raw_scene in enumerate([item for item in scenes if isinstance(item, dict)], start=1):
        scene_id = str(raw_scene.get("scene_mark_id") or f"{shot_id}_scene_{index:03d}").strip()
        start = max(shot_start, min(safe_float(raw_scene.get("start"), shot_start), shot_end))
        end = max(start, min(safe_float(raw_scene.get("end"), start), shot_end))
        single_path = normalized_scene_path(raw_scene, "single_keyframe_path")
        first_path = normalized_scene_path(raw_scene, "first_keyframe_path")
        last_path = normalized_scene_path(raw_scene, "last_keyframe_path")
        selected: list[tuple[str, str]] = []
        if mode == "single":
            path = single_path or first_path or last_path
            if path in by_path:
                selected.append(("single", path))
            else:
                warnings.append(f"{scene_id} has no valid single keyframe path")
                continue
        else:
            if first_path not in by_path and single_path in by_path:
                first_path = single_path
            if last_path not in by_path and single_path in by_path:
                last_path = single_path
            if first_path not in by_path or last_path not in by_path:
                warnings.append(f"{scene_id} has invalid first/last keyframe path")
                continue
            if first_path == last_path:
                warnings.append(f"{scene_id} has identical first/last keyframe path")
                continue
            if first_path in used_keyframe_paths or last_path in used_keyframe_paths:
                warnings.append(f"{scene_id} reuses a keyframe path from another scene")
                continue
            if safe_float(by_path[first_path].get("time"), 0.0) >= safe_float(by_path[last_path].get("time"), 0.0):
                warnings.append(f"{scene_id} first keyframe is not earlier than last keyframe")
                continue
            selected.extend([("first", first_path), ("last", last_path)])
        keyframe_paths = []
        for role, path in selected:
            frame = dict(by_path[path])
            frame["scene_mark"] = {"scene_mark_id": scene_id, "scene_index": index, "role": role, "click_behavior": "show_scene_description"}
            kept_by_path[path] = frame
            keyframe_paths.append(path)
            used_keyframe_paths.add(path)
        matched_srt = str(raw_scene.get("srt_text") or "").strip()
        scene_mark = {
            "scene_mark_id": scene_id,
            "shot_id": shot_id,
            "scene_index": index,
            "mode": mode,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.0, end - start), 3),
            "keyframes": {"single": single_path if mode == "single" else "", "first": first_path if mode != "single" else "", "last": last_path if mode != "single" else "", "paths": keyframe_paths},
            "srt_text": matched_srt or srt_for_range(srt_text, shot_start, start, end),
            "srt_match_reason": str(raw_scene.get("srt_match_reason") or "").strip(),
            "srt_match_source": str(raw_scene.get("srt_match_source") or ("ocr_aligned" if raw_scene.get("ocr_text_used") else "srt_only")).strip(),
            "ocr_text_used": str(raw_scene.get("ocr_text_used") or "").strip(),
            "scene_description": {
                "summary": str(raw_scene.get("summary") or "").strip(),
                "visual_change": str(raw_scene.get("visual_change") or "").strip(),
                "motion_prompt": str(raw_scene.get("motion_prompt") or "").strip(),
                "video_prompt": str(raw_scene.get("video_prompt") or "").strip(),
                "negative_prompt": str(raw_scene.get("negative_prompt") or "watermark, logo, subtitles, captions, unreadable text, distorted face, bad hands, low quality").strip(),
                "model_notes": raw_scene.get("model_notes") if isinstance(raw_scene.get("model_notes"), dict) else {},
            },
            "warnings": raw_scene.get("warnings") if isinstance(raw_scene.get("warnings"), list) else [],
        }
        scene_marks.append(scene_mark)
    kept = sorted(kept_by_path.values(), key=lambda item: (safe_float(item.get("time"), 1_000_000.0), str(item.get("path") or "")))
    return kept, scene_marks, warnings


def process_shot(context: dict[str, Any], shot: dict[str, Any], mode: str, image_workspace: Path, timeout_seconds: int) -> dict[str, Any]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    original_keyframes = compact_keyframes(reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else [])
    if not original_keyframes:
        return {"shot_id": shot.get("shot_id"), "status": "skipped", "message": "No keyframes"}
    model_payload = call_model(context, shot, original_keyframes, mode, image_workspace, timeout_seconds)
    kept, scene_marks, warnings = normalize_model_scenes(shot, model_payload, original_keyframes, mode)
    if not kept or not scene_marks:
        raise RuntimeError(f"No valid scene marks returned for {shot.get('shot_id')}: {warnings}")
    reference["keyframes"] = kept
    reference["scene_marks"] = scene_marks
    reference["scene_mark_summary"] = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "mode": mode, "input_keyframe_count": len(original_keyframes), "output_keyframe_count": len(kept), "scene_count": len(scene_marks), "behavior": "mark_only", "prompt_priority": "keyframe_image_first", "warnings": warnings}
    shot["reference"] = reference
    return {"shot_id": shot.get("shot_id"), "status": "completed", "input_keyframe_count": len(original_keyframes), "output_keyframe_count": len(kept), "scene_count": len(scene_marks), "warnings": warnings}


def scene_mark_keyframes_for_refresh(keyframes: list[dict[str, Any]], scene_marks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path = frame_by_path(compact_keyframes(keyframes))
    selected: dict[str, dict[str, Any]] = {}
    for mark in scene_marks:
        if not isinstance(mark, dict):
            continue
        mark_keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
        for path in [mark_keyframes.get("single"), mark_keyframes.get("first"), mark_keyframes.get("last")]:
            path_value = str(path or "").strip()
            if path_value and path_value in by_path:
                selected[path_value] = by_path[path_value]
    return sorted(selected.values(), key=lambda item: (safe_float(item.get("time"), 1_000_000.0), str(item.get("path") or "")))


def quick_validate_scene_marks(shot: dict[str, Any]) -> list[str]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    keyframes = reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else []
    scene_marks = reference.get("scene_marks") if isinstance(reference.get("scene_marks"), list) else []
    paths = {str(frame.get("path") or "") for frame in keyframes if isinstance(frame, dict) and str(frame.get("path") or "")}
    scene_ids = {str(mark.get("scene_mark_id") or "") for mark in scene_marks if isinstance(mark, dict) and str(mark.get("scene_mark_id") or "")}
    errors: list[str] = []
    seen_roles: set[tuple[str, str]] = set()
    used_paths: set[str] = set()
    for frame in keyframes:
        if not isinstance(frame, dict):
            continue
        mark = frame.get("scene_mark") if isinstance(frame.get("scene_mark"), dict) else None
        if not mark:
            continue
        scene_id = str(mark.get("scene_mark_id") or "")
        role = str(mark.get("role") or "")
        if scene_id not in scene_ids:
            errors.append(f"frame references missing scene_mark_id: {scene_id}")
        role_key = (scene_id, role)
        if role_key in seen_roles:
            errors.append(f"duplicate scene mark role: {scene_id}:{role}")
        seen_roles.add(role_key)
    for mark in scene_marks:
        if not isinstance(mark, dict):
            continue
        scene_id = str(mark.get("scene_mark_id") or "")
        mark_keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
        single = str(mark_keyframes.get("single") or "").strip()
        first = str(mark_keyframes.get("first") or "").strip()
        last = str(mark_keyframes.get("last") or "").strip()
        for label, path in (("single", single), ("first", first), ("last", last)):
            if path and path not in paths:
                errors.append(f"{scene_id} {label} path missing from keyframes: {path}")
        if first or last:
            if not first or not last:
                errors.append(f"{scene_id} has incomplete first/last keyframes")
            if first and last and first == last:
                errors.append(f"{scene_id} first equals last")
            for path in (first, last):
                if not path:
                    continue
                if path in used_paths:
                    errors.append(f"keyframe path reused across scene marks: {path}")
                used_paths.add(path)
        if safe_float(mark.get("duration"), 0.0) < 0:
            errors.append(f"{scene_id} has negative duration")
    return errors


def validate_processed_shot(shot: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode == "none":
        return {"mode": mode, "status": "skipped", "errors": []}
    errors = quick_validate_scene_marks(shot)
    return {"mode": mode, "status": "passed" if not errors else "failed", "errors": errors}


def process_shot_refresh_prompts(context: dict[str, Any], shot: dict[str, Any], image_workspace: Path, timeout_seconds: int) -> dict[str, Any]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    keyframes = reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else []
    scene_marks = reference.get("scene_marks") if isinstance(reference.get("scene_marks"), list) else []
    shot_start = safe_float(shot.get("start"))
    srt_text = str(reference.get("srt_text") or "")
    if not keyframes:
        return {"shot_id": shot.get("shot_id"), "status": "skipped", "message": "No keyframes"}
    if not scene_marks:
        raise RuntimeError(f"{shot.get('shot_id')} has no existing scene_marks to refresh")
    current_paths = {str(frame.get("path") or "") for frame in keyframes if isinstance(frame, dict)}
    valid_scene_marks = []
    skipped_scene_ids = []
    for mark in scene_marks:
        mark_keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
        required_paths = [str(mark_keyframes.get("single") or "").strip()] if mark_keyframes.get("single") else [str(mark_keyframes.get("first") or "").strip(), str(mark_keyframes.get("last") or "").strip()]
        required_paths = [path for path in required_paths if path]
        if required_paths and all(path in current_paths for path in required_paths):
            mark["srt_text"] = srt_for_range(srt_text, shot_start, safe_float(mark.get("start")), safe_float(mark.get("end"), safe_float(mark.get("start"))))
            valid_scene_marks.append(mark)
        else:
            skipped_scene_ids.append(str(mark.get("scene_mark_id") or ""))
    if not valid_scene_marks:
        raise RuntimeError(f"{shot.get('shot_id')} has no valid scene_marks with existing first/last keyframes to refresh")
    refresh_keyframes = scene_mark_keyframes_for_refresh(keyframes, valid_scene_marks)
    if not refresh_keyframes:
        raise RuntimeError(f"{shot.get('shot_id')} has no readable first/last keyframes to refresh")
    payload = call_refresh_model(context, shot, refresh_keyframes, valid_scene_marks, image_workspace, timeout_seconds)
    payload_shot_id = str(payload.get("shot_id") or "").strip()
    current_shot_id = str(shot.get("shot_id") or "").strip()
    if payload_shot_id and payload_shot_id != current_shot_id:
        raise RuntimeError(f"Refresh model returned shot_id {payload_shot_id}, expected {current_shot_id}")
    returned = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
    if not returned:
        raise RuntimeError(f"Refresh model returned no scenes for {shot.get('shot_id')}")
    by_scene_id = {str(item.get("scene_mark_id") or ""): item for item in returned if isinstance(item, dict)}
    positional_by_id: dict[str, dict[str, Any]] = {}
    if len(returned) == len(valid_scene_marks):
        positional_by_id = {
            str(mark.get("scene_mark_id") or ""): raw_scene
            for mark, raw_scene in zip(valid_scene_marks, returned)
            if isinstance(raw_scene, dict)
        }
    refreshed = 0
    for mark in valid_scene_marks:
        scene_id = str(mark.get("scene_mark_id") or "")
        raw_scene = by_scene_id.get(scene_id) or positional_by_id.get(scene_id)
        if not raw_scene:
            continue
        matched_srt = str(raw_scene.get("srt_text") or "").strip()
        mark["srt_text"] = matched_srt or mark.get("srt_text") or srt_for_range(srt_text, shot_start, safe_float(mark.get("start")), safe_float(mark.get("end"), safe_float(mark.get("start"))))
        mark["srt_match_reason"] = str(raw_scene.get("srt_match_reason") or "").strip()
        mark["srt_match_source"] = str(raw_scene.get("srt_match_source") or ("ocr_aligned" if raw_scene.get("ocr_text_used") else "srt_only")).strip()
        mark["ocr_text_used"] = str(raw_scene.get("ocr_text_used") or "").strip()
        mark["scene_description"] = {
            "summary": str(raw_scene.get("summary") or "").strip(),
            "visual_change": str(raw_scene.get("visual_change") or "").strip(),
            "motion_prompt": str(raw_scene.get("motion_prompt") or "").strip(),
            "video_prompt": str(raw_scene.get("video_prompt") or "").strip(),
            "negative_prompt": str(raw_scene.get("negative_prompt") or "watermark, logo, subtitles, captions, unreadable text, distorted face, bad hands, low quality").strip(),
            "model_notes": raw_scene.get("model_notes") if isinstance(raw_scene.get("model_notes"), dict) else {},
        }
        mark["prompt_source"] = "model_refresh"
        mark["prompt_priority"] = "keyframe_image_first"
        mark["prompt_refreshed_at"] = int(time.time() * 1000)
        if isinstance(raw_scene.get("warnings"), list):
            mark["warnings"] = raw_scene["warnings"]
        refreshed += 1
    if refreshed <= 0:
        returned_ids = [str(item.get("scene_mark_id") or "") for item in returned if isinstance(item, dict)]
        expected_ids = [str(mark.get("scene_mark_id") or "") for mark in valid_scene_marks]
        raise RuntimeError(f"Refresh model returned scenes but none matched {shot.get('shot_id')}: expected={expected_ids}, returned={returned_ids}")
    summary = reference.get("scene_mark_summary") if isinstance(reference.get("scene_mark_summary"), dict) else {}
    reference["scene_marks"] = valid_scene_marks
    reference["scene_mark_summary"] = {**summary, "tool": TOOL_NAME, "tool_version": TOOL_VERSION, "mark_mode": "refresh_prompts", "prompt_priority": "keyframe_image_first", "prompt_refreshed_at": int(time.time() * 1000), "scene_count": len(valid_scene_marks), "skipped_scene_marks": skipped_scene_ids}
    shot["reference"] = reference
    return {"shot_id": shot.get("shot_id"), "status": "completed", "mark_mode": "refresh_prompts", "refreshed_scene_count": refreshed, "scene_count": len(valid_scene_marks), "skipped_scene_marks": skipped_scene_ids}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mark shot micro-scenes and compact keyframes using the OC-Rebuild Task run model.")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", required=True, type=int)
    parser.add_argument("--shot-id", action="append", default=[], help="Shot id to process. Can be repeated. Defaults to all shots.")
    parser.add_argument("--mode", choices=MODES, default="first_last")
    parser.add_argument("--mark-mode", choices=MARK_MODES, default="rebuild", help="rebuild lets the model choose scene boundaries and prompts; refresh_prompts keeps existing first/last frames and only refreshes prompts.")
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--restore-source-keyframes", action="store_true", help="Restore Scene Detect keyframes from source_package before marking. Defaults to preserving currently saved shot keyframes.")
    parser.add_argument("--validate", choices=VALIDATE_MODES, default="quick", help="Validation level after processing. quick checks JSON/path consistency only; none skips validation; full currently runs the same lightweight checks without extra model calls.")
    parser.add_argument("--sidecar-output", default="scene_marks.json")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    try:
        database_url = args.database_url or os.environ.get(str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL
        context = REBUILD_03.fetch_context(database_url, args.task_id)
        plan = read_json(workspace / args.input)
        source_package = read_json(workspace / args.source_package)
        image_workspace = Path(str(source_package.get("workspace") or workspace)).expanduser().resolve()
        shots = plan.get("shots") if isinstance(plan.get("shots"), list) else []
        target_ids = {str(item) for item in (args.shot_id or []) if str(item).strip()}
        results = []
        sidecar_shots = []
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            shot_id = str(shot.get("shot_id") or "")
            if target_ids and shot_id not in target_ids:
                continue
            try:
                attach_source_ocr_to_shot(shot, source_package)
                if args.restore_source_keyframes:
                    restore_shot_keyframes_from_source_package(shot, source_package)
                    attach_source_ocr_to_shot(shot, source_package)
                if str(args.mark_mode) == "refresh_prompts":
                    result = process_shot_refresh_prompts(context, shot, image_workspace, int(args.timeout_seconds))
                else:
                    result = process_shot(context, shot, str(args.mode), image_workspace, int(args.timeout_seconds))
                validation = validate_processed_shot(shot, str(args.validate))
                result["validation"] = validation
                if validation.get("status") == "failed":
                    result["status"] = "failed"
                    result["message"] = "; ".join(validation.get("errors") or [])
            except Exception as exc:
                if target_ids and len(target_ids) == 1:
                    raise
                result = {"shot_id": shot_id, "status": "failed", "message": str(exc)}
            results.append(result)
            reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
            sidecar_shots.append({"shot_id": shot_id, "scene_marks": reference.get("scene_marks") or [], "summary": reference.get("scene_mark_summary") or {}})
        if not results:
            raise RuntimeError("No shots matched --shot-id" if target_ids else "No shots found")
        plan["tool_chain"] = [*(plan.get("tool_chain") if isinstance(plan.get("tool_chain"), list) else []), {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "mode": args.mode, "mark_mode": args.mark_mode, "processed_shots": [item.get("shot_id") for item in results], "generated_at": int(time.time() * 1000)}]
        write_json(workspace / args.output, plan)
        sidecar = {"version": 1, "tool": TOOL_NAME, "tool_version": TOOL_VERSION, "task": plan.get("task") or {}, "mode": args.mode, "mark_mode": args.mark_mode, "shots": sidecar_shots, "results": results}
        write_json(workspace / args.sidecar_output, sidecar)
        status = "completed_with_errors" if any(item.get("status") == "failed" for item in results) else "completed"
        result_payload = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": status, "mode": args.mode, "mark_mode": args.mark_mode, "validate": args.validate, "output": args.output, "sidecar_output": args.sidecar_output, "results": results}
    except Exception as exc:
        result_payload = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "failed", "message": str(exc)}
    if args.print_json:
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
    if result_payload["status"] not in {"completed", "completed_with_errors"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
