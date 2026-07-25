#!/usr/bin/env python3
from __future__ import annotations

import base64
import html
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


OPENCREW_REPO = Path(__file__).resolve().parents[2]
if str(OPENCREW_REPO) not in sys.path:
    sys.path.insert(0, str(OPENCREW_REPO))
from scripts.opencrew_paths import opencrew_session_workspace

WORKSPACE = Path(os.environ.get("KOUBO_PLAN_CASE_WORKSPACE", str(opencrew_session_workspace(5)))).expanduser()
DOC_DIR = Path(os.environ.get("KOUBO_PLAN_CASE_DOC_DIR", str(OPENCREW_REPO / "docs/SessionDesign-R2"))).expanduser()
OUT_DIR = DOC_DIR / "koubo_plan_case_actual_results"
WORKING_REL = "SessionOutput/storyboard/Working"
SOURCE_RELS = [
    "SessionOutput/storyboard/srt_storyboard.json",
    "S7_04_02_StoryBoard/Output/srt_storyboard.json",
]
EDIT_REL = "SessionOutput/storyboard/koubo_storyboard_edit.json"
PLAN_FILES = [
    "SessionOutput/storyboard/video_generation_plan.json",
    "SessionOutput/storyboard/image_generation_plan.json",
    "SessionOutput/storyboard/video_only_generation_plan.json",
    "S8_05_01_VideoPlanGenerator/Output/video_generation_plan.json",
    "S8_05_03_ImagePlanGenerator/Output/image_generation_plan.json",
    "S8_05_05_VideoOnlyPlanGenerator/Output/video_only_generation_plan.json",
]

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
WAV_MIN = (
    b"RIFF$\x00\x00\x00WAVEfmt "
    b"\x10\x00\x00\x00\x01\x00\x01\x00@\x1f\x00\x00@\x1f\x00\x00\x01\x00\x08\x00data\x00\x00\x00\x00"
)
MP4_MARKER = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


def now_ms() -> int:
    return int(time.time() * 1000)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def rel_path(asset_key: str, suffix: str) -> str:
    return f"{WORKING_REL}/{asset_key}_{suffix}"


def file_exists(rel: str) -> bool:
    if not rel:
        return False
    p = WORKSPACE / rel
    return p.exists() and p.is_file() and p.stat().st_size > 0


def case_file_exists(result: dict[str, Any], rel: str) -> bool:
    return str(rel or "") in set(result.get("existing_files") or [])


def ensure_file(rel: str, kind: str) -> None:
    path = WORKSPACE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "image":
        path.write_bytes(PNG_1X1)
    elif kind == "audio":
        path.write_bytes(WAV_MIN)
    elif kind == "video":
        path.write_bytes(MP4_MARKER)
    elif kind == "json":
        write_json(path, {"status": "test_fixture", "created_at": now_ms()})
    else:
        path.write_bytes(b"fixture")


@dataclass
class DialogueSpec:
    label: str
    seconds: float
    slots: list[int]
    image_prompt: bool = False
    video_prompt: bool = False
    tail: bool = False
    talking_head: bool = True


@dataclass
class SceneSpec:
    title: str
    dialogues: list[DialogueSpec]


@dataclass
class ShotSpec:
    title: str
    scenes: list[SceneSpec]


@dataclass
class CaseSpec:
    suite: str
    number: int
    title: str
    target_type: str
    max_seconds: float
    min_seconds: float
    tolerance: float
    shots: list[ShotSpec]
    shot_id: str = "shot_001"
    scene_id: str = "scene_001"
    execution: dict[str, str] = field(default_factory=dict)


def empty_working() -> None:
    working = WORKSPACE / WORKING_REL
    working.mkdir(parents=True, exist_ok=True)
    for path in working.glob("*.jpg"):
        path.unlink()
    for path in working.glob("*.png"):
        path.unlink()
    for path in working.glob("*.wav"):
        path.unlink()
    for path in working.glob("*.mp4"):
        path.unlink()
    for path in working.glob("*.json"):
        if "Prompt" in path.name:
            path.unlink()
    for rel in PLAN_FILES:
        path = WORKSPACE / rel
        if path.exists():
            path.unlink()
    for rel in (
        "SessionOutput/storyboard/video_plan_ui_cache.json",
        "SessionOutput/storyboard/video_plan_execution_state.json",
        "SessionOutput/storyboard/image_plan_execution_state.json",
        "SessionOutput/storyboard/video_only_plan_execution_state.json",
        "SessionOutput/storyboard/video_plan_execution_result.json",
        "SessionOutput/storyboard/image_plan_execution_result.json",
        "SessionOutput/storyboard/video_only_plan_execution_result.json",
    ):
        path = WORKSPACE / rel
        if path.exists():
            path.unlink()


def build_storyboard(case: CaseSpec) -> dict[str, Any]:
    shots = []
    global_scene_index = 0
    global_dialogue_index = 0
    for shot_index, shot_spec in enumerate(case.shots, start=1):
        shot_id = f"shot_{shot_index:03d}"
        shot_start = 0.0
        shot_scenes = []
        for scene_index, scene_spec in enumerate(shot_spec.scenes, start=1):
            global_scene_index += 1
            scene_id = f"scene_{global_scene_index:03d}"
            scene_dialogues = []
            cursor = shot_start
            for dialogue_index, spec in enumerate(scene_spec.dialogues, start=1):
                global_dialogue_index += 1
                asset_key = f"srt_{global_dialogue_index:04d}"
                start = cursor
                end = cursor + spec.seconds
                cursor = end
                audio_rel = rel_path(asset_key, "Audio_Final.wav")
                source_rel = rel_path(asset_key, "Image_Source.jpg")
                image_rel = rel_path(asset_key, "Image_New.png")
                raw_rel = rel_path(asset_key, "Video_Raw.mp4")
                final_rel = rel_path(asset_key, "Video_Final.mp4")
                tail_rel = rel_path(asset_key, "TailFrame.jpg")
                prompt_image_rel = rel_path(asset_key, "ImagePrompt.json")
                prompt_video_rel = rel_path(asset_key, "VideoPrompt.json")
                a, o, n, v, f = spec.slots
                if a:
                    ensure_file(audio_rel, "audio")
                if o:
                    ensure_file(source_rel, "image")
                if n:
                    ensure_file(image_rel, "image")
                if v:
                    ensure_file(raw_rel, "video")
                if f:
                    ensure_file(final_rel, "video")
                if spec.tail:
                    ensure_file(tail_rel, "image")
                if spec.image_prompt:
                    ensure_file(prompt_image_rel, "json")
                if spec.video_prompt:
                    ensure_file(prompt_video_rel, "json")
                working_assets = {
                    "audio": {"slot": "Audio_Final", "source_type": "generated", "path": audio_rel if a else ""},
                    "images": [
                        {"slot": "Image_New", "source_type": "generated", "path": image_rel if n else ""},
                        {"slot": "Image_02", "source_type": "", "path": ""},
                    ],
                    "video": {"slot": "Video_Final", "source_type": "generated", "path": final_rel if f else ""},
                }
                dialogue = {
                    "dialogue_id": f"{scene_id}_dialogue_{dialogue_index:03d}",
                    "srt_id": asset_key,
                    "srt_ids": [asset_key],
                    "dialogue_asset_key": asset_key,
                    "dialogue": f"{spec.label} 测试对白",
                    "text": f"{spec.label} 测试对白",
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(spec.seconds, 3),
                    "image_path": source_rel if o else "",
                    "source_image_paths": [source_rel] if o else [],
                    "bound_image_path": image_rel if n else "",
                    "working_assets": working_assets,
                    "video_plan": {"is_talking_head": bool(spec.talking_head)},
                }
                scene_dialogues.append(dialogue)
            scene_start = scene_dialogues[0]["start"] if scene_dialogues else shot_start
            scene_end = scene_dialogues[-1]["end"] if scene_dialogues else shot_start
            shot_scenes.append({
                "scene_id": scene_id,
                "source_scene_id": scene_id,
                "title": scene_spec.title,
                "scene_name": scene_spec.title,
                "summary": scene_spec.title,
                "start": scene_start,
                "end": scene_end,
                "duration": round(scene_end - scene_start, 3),
                "dialogue_items": scene_dialogues,
                "dialogues": scene_dialogues,
                "working_assets": {
                    "audio": {"slot": "Audio_Final", "path": ""},
                    "images": [{"slot": "Image_New", "path": ""}, {"slot": "Image_02", "path": ""}],
                    "video": {"slot": "Video_Final", "path": ""},
                },
            })
            shot_start = scene_end
        shots.append({
            "shot_id": shot_id,
            "source_shot_id": shot_id,
            "title": shot_spec.title,
            "shot_name": shot_spec.title,
            "summary": shot_spec.title,
            "start": 0,
            "end": round(shot_start, 3),
            "duration": round(shot_start, 3),
            "scenes": shot_scenes,
        })
    return {
        "schema_version": "analysis_v1_srt_storyboard_0.2",
        "tool": "koubo_plan_case_fixture",
        "tool_version": "0.1",
        "analysis_task_id": 4,
        "analysis_session_id": 5,
        "video_formula": "口播测试",
        "created_at": now_ms(),
        "updated_at": now_ms(),
        "shots": shots,
    }


def write_case_fixture(case: CaseSpec) -> dict[str, Any]:
    empty_working()
    storyboard = build_storyboard(case)
    for rel in SOURCE_RELS:
        write_json(WORKSPACE / rel, storyboard)
    edit_path = WORKSPACE / EDIT_REL
    if edit_path.exists():
        edit_path.unlink()
    write_json(WORKSPACE / "SessionOutput/storyboard/video_plan_settings.json", {
        "max_video_seconds": case.max_seconds,
        "min_video_seconds": case.min_seconds,
        "split_tolerance_seconds": case.tolerance,
        "task_id": 4,
        "session_id": 5,
        "updated_at": now_ms(),
    })
    return storyboard


def dialogue_label_map(storyboard: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for shot in storyboard.get("shots") or []:
        for scene in shot.get("scenes") or []:
            for dialogue in scene.get("dialogue_items") or []:
                srt_id = str(dialogue.get("srt_id") or "")
                text = str(dialogue.get("text") or dialogue.get("dialogue") or "")
                label = text.replace(" 测试对白", "").strip()
                if srt_id and label:
                    labels[srt_id] = label
    return labels


def run_tool(script: str, case: CaseSpec) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(OPENCREW_REPO / f"ToolLibrary/Analysis_V1/{script}"),
        "--workspace",
        str(WORKSPACE),
        "--target-type",
        case.target_type,
        "--max-video-seconds",
        str(case.max_seconds),
        "--min-video-seconds",
        str(case.min_seconds),
        "--split-tolerance-seconds",
        str(case.tolerance),
        "--force",
        "--print-json",
    ]
    if case.target_type in {"scene", "shot"}:
        cmd += ["--shot-id", case.shot_id]
    if case.target_type == "scene":
        cmd += ["--scene-id", case.scene_id]
    completed = subprocess.run(cmd, cwd=str(OPENCREW_REPO), text=True, capture_output=True)
    report_rel = {
        "05_01_VideoPlanGenerator.py": "S8_05_01_VideoPlanGenerator/Report/Result.json",
        "05_03_ImagePlanGenerator.py": "S10_05_03_ImagePlanGenerator/Report/Result.json",
        "05_05_VideoOnlyPlanGenerator.py": "S12_05_05_VideoOnlyPlanGenerator/Report/Result.json",
    }[script]
    payload = read_json(WORKSPACE / report_rel)
    if not payload:
        try:
            payload = json.loads(completed.stdout.strip().splitlines()[-1]) if completed.stdout.strip() else {}
        except Exception:
            payload = {"status": "failed", "stdout": completed.stdout[-2000:], "stderr": completed.stderr[-2000:]}
    payload["_returncode"] = completed.returncode
    if completed.returncode != 0:
        payload["_stderr"] = completed.stderr[-3000:]
    return payload


def run_case(case: CaseSpec) -> dict[str, Any]:
    storyboard = write_case_fixture(case)
    labels = dialogue_label_map(storyboard)
    video_result = run_tool("05_01_VideoPlanGenerator.py", case)
    existing_files = sorted(
        str(path.relative_to(WORKSPACE))
        for path in (WORKSPACE / WORKING_REL).glob("*")
        if path.is_file() and path.stat().st_size > 0
    )
    if case.suite == "video":
        plan_rel = "SessionOutput/storyboard/video_generation_plan.json"
        return {"case": case, "tool_result": video_result, "plan": read_json(WORKSPACE / plan_rel), "existing_files": existing_files, "labels": labels}
    if case.suite == "image":
        image_result = run_tool("05_03_ImagePlanGenerator.py", case)
        existing_files = sorted(
            str(path.relative_to(WORKSPACE))
            for path in (WORKSPACE / WORKING_REL).glob("*")
            if path.is_file() and path.stat().st_size > 0
        )
        return {"case": case, "tool_result": image_result, "video_result": video_result, "plan": read_json(WORKSPACE / "SessionOutput/storyboard/image_generation_plan.json"), "existing_files": existing_files, "labels": labels}
    video_only_result = run_tool("05_05_VideoOnlyPlanGenerator.py", case)
    existing_files = sorted(
        str(path.relative_to(WORKSPACE))
        for path in (WORKSPACE / WORKING_REL).glob("*")
        if path.is_file() and path.stat().st_size > 0
    )
    return {"case": case, "tool_result": video_only_result, "video_result": video_result, "plan": read_json(WORKSPACE / "SessionOutput/storyboard/video_only_generation_plan.json"), "existing_files": existing_files, "labels": labels}


def state_color(done: bool, pending: bool = False, running: bool = False, failed: bool = False) -> str:
    if done:
        return "green"
    if running:
        return "yellow"
    if failed:
        return "red"
    if pending:
        return "white"
    return "gray"


def slot(label: str, color: str) -> str:
    color_zh = {"green": "绿", "white": "白", "gray": "灰", "yellow": "黄", "red": "红"}[color]
    return f'<span class="slot {color}">{html.escape(label)}{color_zh}</span>'


def duration_label(seconds: Any) -> str:
    try:
        value = float(seconds)
    except Exception:
        return ""
    return f"{value:g}s"


def dialogue_labels(result: dict[str, Any], ids: list[Any]) -> str:
    mapping = result.get("labels") or {}
    values = [str(mapping.get(str(item), item)) for item in ids]
    return ", ".join(values)


def compact_segment_id(value: Any) -> str:
    text = str(value or "Segment")
    text = text.replace("shot_001_", "").replace("shot_002_", "")
    text = text.replace("scene_001_", "S1C1-").replace("scene_002_", "S1C2-").replace("scene_003_", "S2C1-").replace("scene_004_", "S2C2-")
    text = text.replace("_segment_", "-S")
    return text


def plan_segments(plan: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    rows = []
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            for segment in scene.get("segments") or []:
                rows.append((f"{shot.get('shot_id')} / {scene.get('scene_id')}", scene, segment))
    return rows


def render_video_actual(result: dict[str, Any]) -> str:
    case: CaseSpec = result["case"]
    plan = result["plan"]
    parts = []
    for scope, _scene, segment in plan_segments(plan):
        status = str(segment.get("status") or "")
        tasks = segment.get("tasks") or {}
        outputs = segment.get("planned_outputs") or {}
        first = segment.get("first_frame") or {}
        tail = segment.get("tail_frame") or {}
        asset_key = str(segment.get("asset_key") or "")
        audio_tasks = [a for a in segment.get("dialogue_audio_tasks") or [] if isinstance(a, dict)]
        audio_done = bool(audio_tasks) and all(case_file_exists(result, str(a.get("existing_audio_path") or a.get("planned_audio_path") or "")) for a in audio_tasks)
        image_path = str(outputs.get("image_path") or first.get("planned_generated_image_path") or rel_path(asset_key, "Image_New.png"))
        raw_path = str(outputs.get("raw_video_path") or rel_path(asset_key, "Video_Raw.mp4"))
        final_path = str(outputs.get("final_video_path") or outputs.get("video_path") or rel_path(asset_key, "Video_Final.mp4"))
        tail_path = str(tail.get("planned_path") or rel_path(asset_key, "TailFrame.jpg"))
        source_type = str(first.get("source_type") or "")
        deps = segment.get("dependencies") or {}
        if status in {"blocked", "skipped"}:
            slots = [
                slot("音频", "gray"),
                slot("首帧", "red" if status == "blocked" else "gray"),
                slot("新视频", "gray"),
                slot("终视频", "gray"),
                slot("尾帧", "gray"),
            ]
        else:
            audio_color = state_color(audio_done, pending=bool(tasks.get("need_audio")))
            if source_type in {"previous_segment_tail_frame", "previous_scene_tail_frame"}:
                label = "尾帧"
                frame_source = deps.get("depends_on_segment_id") or Path(str(deps.get("depends_on_tail_frame_path") or "")).stem
                if frame_source:
                    label += f" {compact_segment_id(frame_source)}"
                frame_color = state_color(case_file_exists(result, str(deps.get("depends_on_tail_frame_path") or first.get("source_path") or "")), pending=False)
            elif source_type == "bound_video":
                label = "新图"
                frame_color = "gray"
            else:
                label = "新图"
                frame_color = state_color(case_file_exists(result, image_path), pending=bool(tasks.get("need_image")))
            raw_color = state_color(case_file_exists(result, raw_path), pending=bool(tasks.get("need_video") and case_file_exists(result, image_path)))
            final_color = state_color(case_file_exists(result, final_path), pending=bool(case_file_exists(result, raw_path) and not case_file_exists(result, final_path)))
            tail_color = state_color(case_file_exists(result, tail_path), pending=bool(case_file_exists(result, final_path) and not case_file_exists(result, tail_path)))
            slots = [slot("音频", audio_color), slot(label, frame_color), slot("新视频", raw_color), slot("终视频", final_color), slot("尾帧", tail_color)]
        dialogue_ids = dialogue_labels(result, segment.get("dialogue_ids") or [])
        task_type = "对口型" if (tasks.get("sync_mode") == "lipsync") else ("替换音频" if tasks.get("sync_mode") else "")
        parts.append(f"""
          <div class="vp-segment">
            <span class="segment-id">{html.escape(compact_segment_id(segment.get('segment_id') or 'Segment'))}</span>
            <div>
              <div class="segment-dialogues">范围：{html.escape(scope)} / 包含 D：{html.escape(dialogue_ids)} / {duration_label(segment.get('duration'))}</div>
              <div class="slot-row">{''.join(slots)}</div>
              <div class="expect">source_type={html.escape(str(first.get('source_type') or ''))}{' / 终视频任务=' + task_type if task_type else ''}</div>
            </div>
          </div>""")
    if not parts:
        parts.append('<div class="vp-segment"><span class="segment-id">无</span><div><div class="slot-row">' + slot("无 Segment", "red") + "</div></div></div>")
    return case_shell(case, f"{len(parts)} 个实际 Segment", "".join(parts), result)


def source_segment_from_task(task: dict[str, Any]) -> dict[str, Any]:
    return task.get("source_segment") if isinstance(task.get("source_segment"), dict) else {}


def render_image_actual(result: dict[str, Any]) -> str:
    case: CaseSpec = result["case"]
    plan = result["plan"]
    parts = []
    for task in plan.get("image_tasks") or []:
        asset_key = str(task.get("asset_key") or "")
        prompt_path = str(task.get("image_prompt_path") or rel_path(asset_key, "ImagePrompt.json"))
        image_path = str(task.get("image_path") or rel_path(asset_key, "Image_New.png"))
        source_segment = source_segment_from_task(task)
        first = source_segment.get("first_frame") if isinstance(source_segment.get("first_frame"), dict) else {}
        source_exists = case_file_exists(result, str(first.get("source_path") or ""))
        raw_exists = case_file_exists(result, rel_path(asset_key, "Video_Raw.mp4"))
        final_exists = case_file_exists(result, rel_path(asset_key, "Video_Final.mp4"))
        image_exists = case_file_exists(result, image_path)
        exec_state = case.execution.get("image")
        prompt_color = state_color(case_file_exists(result, prompt_path), pending=bool(source_exists and not image_exists and not raw_exists and not final_exists))
        image_color = state_color(
            image_exists,
            pending=bool(source_exists and case_file_exists(result, prompt_path) and not raw_exists and not final_exists),
            running=exec_state == "running",
            failed=exec_state == "failed",
        )
        ds = dialogue_labels(result, source_segment.get("dialogue_ids") or task.get("dialogue_ids") or [])
        parts.append(f"""
          <div class="vp-segment">
            <span class="segment-id">{html.escape(str(task.get('image_task_id') or asset_key))}</span>
            <div>
              <div class="segment-dialogues">包含 D：{html.escape(ds)} / {duration_label(source_segment.get('duration') or task.get('duration'))}</div>
              <div class="slot-row">{slot('提示词', prompt_color)}{slot('新图', image_color)}</div>
            </div>
          </div>""")
    return case_shell(case, f"{len(parts)} 个实际 Image Task", "".join(parts), result)


def render_video_only_actual(result: dict[str, Any]) -> str:
    case: CaseSpec = result["case"]
    plan = result["plan"]
    tasks = plan.get("video_only_tasks") or plan.get("tasks") or []
    parts = []
    for task in tasks:
        asset_key = str(task.get("asset_key") or "")
        source_segment = source_segment_from_task(task)
        prompt_path = str(task.get("video_prompt_path") or rel_path(asset_key, "VideoPrompt.json"))
        image_path = str(task.get("first_frame_path") or task.get("image_path") or rel_path(asset_key, "Image_New.png"))
        raw_path = str(task.get("raw_video_path") or rel_path(asset_key, "Video_Raw.mp4"))
        final_path = str(task.get("final_video_path") or rel_path(asset_key, "Video_Final.mp4"))
        audio_tasks = [a for a in source_segment.get("dialogue_audio_tasks") or [] if isinstance(a, dict)]
        audio_done = bool(audio_tasks) and all(case_file_exists(result, str(a.get("existing_audio_path") or a.get("planned_audio_path") or "")) for a in audio_tasks)
        exec_state = case.execution.get("raw")
        audio_color = state_color(audio_done, pending=not audio_done)
        image_color = state_color(case_file_exists(result, image_path), pending=False)
        prompt_color = state_color(case_file_exists(result, prompt_path), pending=bool(case_file_exists(result, image_path) and not case_file_exists(result, raw_path) and not case_file_exists(result, final_path)))
        raw_color = state_color(case_file_exists(result, raw_path), pending=bool(case_file_exists(result, image_path) and case_file_exists(result, prompt_path) and not case_file_exists(result, final_path)), running=exec_state == "running", failed=exec_state == "failed")
        final_color = state_color(case_file_exists(result, final_path), pending=bool(case_file_exists(result, raw_path) and not case_file_exists(result, final_path)))
        ds = dialogue_labels(result, source_segment.get("dialogue_ids") or task.get("dialogue_ids") or [])
        parts.append(f"""
          <div class="vp-segment">
            <span class="segment-id">{html.escape(str(task.get('video_only_task_id') or asset_key))}</span>
            <div>
              <div class="segment-dialogues">包含 D：{html.escape(ds)} / {duration_label(source_segment.get('duration') or task.get('duration'))}</div>
              <div class="slot-row">{slot('音频', audio_color)}{slot('新图', image_color)}{slot('提示词', prompt_color)}{slot('新视频', raw_color)}{slot('拷贝终视频', final_color)}</div>
            </div>
          </div>""")
    return case_shell(case, f"{len(parts)} 个实际 Video Only Task", "".join(parts), result)


def case_shell(case: CaseSpec, summary: str, body: str, result: dict[str, Any]) -> str:
    status = str((result.get("tool_result") or {}).get("status") or "unknown")
    return f"""
      <section class="actual-case" id="{case.suite}-{case.number:02d}">
        <div class="compare-title">
          <strong>{html.escape(case.title)}</strong>
          <span class="pill">实际运行 / {html.escape(summary)} / tool={html.escape(status)}</span>
        </div>
        <div class="actual-body">{body}</div>
      </section>
    """


def base_style() -> str:
    return """
    *{box-sizing:border-box}body{margin:0;padding:18px;background:#f5f7fb;color:#172033;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}
    .actual-case{width:1160px;background:#fff;border:1px solid #d4deec;border-radius:8px;padding:14px}
    .compare-title{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:12px;font-size:16px}
    .pill{border:1px solid #d4deec;background:#f3f6fa;border-radius:999px;padding:5px 10px;color:#39475e;font-size:12px;font-weight:700}
    .vp-segment{display:grid;grid-template-columns:230px 1fr;gap:12px;align-items:center;border:1px solid #d4deec;border-radius:8px;padding:10px;margin:8px 0;background:#fff}
    .segment-id{font-weight:800;color:#172033;overflow-wrap:anywhere}.segment-dialogues{font-weight:700;color:#344258;margin-bottom:7px;font-size:13px}
    .slot-row{display:flex;flex-wrap:wrap;gap:7px;align-items:center}
    .slot{display:inline-flex;align-items:center;min-height:25px;border-radius:999px;padding:4px 10px;border:1px solid #d7e0eb;background:#eef2f6;color:#2f3b4e;font-weight:800;font-size:12px;white-space:nowrap}
    .slot.white{background:#fff;color:#172033;border-color:#cbd7e6}.slot.green{background:#dcf7e8;color:#08733f;border-color:#9be0b8}
    .slot.yellow{background:#fff1c2;color:#8b5a00;border-color:#e4c158}.slot.red{background:#ffe3e1;color:#b42318;border-color:#ffa6a0}
    .expect{margin-top:7px;color:#52627a;font-size:12px;line-height:1.45;border-top:1px dashed #d0dae8;padding-top:7px}
    """


def write_actual_html(results: list[dict[str, Any]]) -> Path:
    cards = []
    for result in results:
        suite = result["case"].suite
        if suite == "video":
            cards.append(render_video_actual(result))
        elif suite == "image":
            cards.append(render_image_actual(result))
        else:
            cards.append(render_video_only_actual(result))
    path = OUT_DIR / "actual_cases.html"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>{base_style()}</style></head><body>{''.join(cards)}</body></html>", encoding="utf-8")
    return path


def ds(count: int, seconds: float, start: int = 1, slots: list[int] | None = None, **kw: Any) -> list[DialogueSpec]:
    slots = slots or [0, 0, 0, 0, 0]
    return [DialogueSpec(f"D{index}", seconds, slots[:] if index == start else [0, 0, 0, 0, 0], **kw) for index in range(1, count + 1)]


def one_scene(dialogues: list[DialogueSpec]) -> list[ShotSpec]:
    return [ShotSpec("Shot1", [SceneSpec("Scene1", dialogues)])]


def cross_scene(prefix: str, specs: list[tuple[list[int], dict[str, Any]]]) -> SceneSpec:
    return SceneSpec(prefix, [DialogueSpec(f"{prefix}-D{i}", 2, slots, **opts) for i, (slots, opts) in enumerate(specs, start=1)])


def cross_shots(scenes: list[SceneSpec]) -> list[ShotSpec]:
    return [ShotSpec("Shot1", scenes[:2]), ShotSpec("Shot2", scenes[2:])]


def cases() -> list[CaseSpec]:
    out: list[CaseSpec] = []
    add = out.append
    add(CaseSpec("video", 1, "Case 1：按时长拆分 + 尾帧连续", "scene", 4, 2, 0, one_scene(ds(5, 2, slots=[0,1,0,0,0]))))
    d2 = ds(5, 1, slots=[0,1,0,0,0]); d2[2].slots = [0,0,1,0,0]
    add(CaseSpec("video", 2, "Case 2：视觉锚点切 1 刀，中间新图重开 Segment", "scene", 5, 1, 0, one_scene(d2)))
    d3 = ds(5, 1, start=3, slots=[0,1,0,0,0])
    add(CaseSpec("video", 3, "Case 3：首句缺视觉，不能从中间偷拆", "scene", 5, 1, 0, one_scene(d3)))
    add(CaseSpec("video", 4, "Case 4：时长阈值，max=4s 拆成 3 段", "scene", 4, 2, 0, one_scene(ds(5, 2, slots=[0,1,0,0,0]))))
    add(CaseSpec("video", 5, "Case 5：时长阈值，max=8s 拆成 2 段", "scene", 8, 2, 0, one_scene(ds(5, 2, slots=[0,1,0,0,0]))))
    add(CaseSpec("video", 6, "Case 6：时长阈值，max=15s 不拆分", "scene", 15, 2, 0, one_scene(ds(5, 2, slots=[0,1,0,0,0]))))
    d7 = ds(5, 1, slots=[0,1,0,0,0]); d7[2].slots = [0,1,0,0,0]; d7[4].slots = [0,0,1,0,0]
    add(CaseSpec("video", 7, "Case 7：视觉锚点切 2 刀，原图 + 新图连续重开 Segment", "scene", 10, 1, 0, one_scene(d7)))
    d8 = ds(5, 2, slots=[1,1,0,0,0]); d8[1].slots = [1,0,0,0,0]
    add(CaseSpec("video", 8, "Case 8：音频合并完成，D1 + D2 都有音频", "scene", 4, 2, 0, one_scene(d8)))
    d9 = ds(5, 2, slots=[1,1,0,0,0])
    add(CaseSpec("video", 9, "Case 9：首个 D 有音频，但后续 D 缺音频", "scene", 4, 2, 0, one_scene(d9)))
    d10 = ds(5, 2, slots=[0,1,0,0,0]); d10[1].slots = [1,0,0,0,0]
    add(CaseSpec("video", 10, "Case 10：首个 D 缺音频，但后续 D 有音频", "scene", 4, 2, 0, one_scene(d10)))
    d11 = ds(5, 2, slots=[0,1,1,1,0]); d11[2].slots = [0,0,0,1,0]
    add(CaseSpec("video", 11, "Case 11：只有新视频，终视频待确认", "scene", 4, 2, 0, one_scene(d11)))
    add(CaseSpec("video", 12, "Case 12：终视频已完成，尾帧待抽取", "scene", 2, 1, 0, one_scene(ds(4, 1, slots=[0,1,1,1,1]))))
    d13 = ds(4, 1, slots=[0,1,1,1,1]); d13[0].tail = True
    add(CaseSpec("video", 13, "Case 13：终视频和尾帧都已完成", "scene", 2, 1, 0, one_scene(d13)))
    d14 = ds(5, 1, slots=[0,1,0,0,0]); d14[2].slots = [0,0,0,0,1]
    add(CaseSpec("video", 14, "Case 14：绑定终视频作为视觉锚点重开 Segment", "scene", 5, 1, 0, one_scene(d14)))
    add(CaseSpec("video", 15, "Case 15：口播首帧，终视频任务 = 对口型", "scene", 15, 1, 0, one_scene(ds(5, 1, slots=[0,1,0,0,0], talking_head=True))))
    add(CaseSpec("video", 16, "Case 16：空镜首帧，终视频任务 = 替换音频", "scene", 15, 1, 0, one_scene(ds(5, 1, slots=[0,1,0,0,0], talking_head=False))))

    empty = [([0,0,0,0,0], {})] * 3
    add(CaseSpec("video", 17, "Case 17：Task 范围，计划尾帧跨 Shot 连续", "task", 4, 2, 0, cross_shots([
        cross_scene("S1C1", [([0,1,0,0,0], {})] + empty[1:]), cross_scene("S1C2", empty), cross_scene("S2C1", empty), cross_scene("S2C2", empty)
    ])))
    upstream_done = [([0,0,1,1,1], {"tail": True}), ([0,0,0,0,0], {}), ([0,0,0,1,1], {"tail": True})]
    add(CaseSpec("video", 18, "Case 18：只生成 Shot2，继承 Shot1 已存在尾帧", "shot", 4, 2, 0, cross_shots([
        cross_scene("S1C1", upstream_done), cross_scene("S1C2", upstream_done), cross_scene("S2C1", empty), cross_scene("S2C2", empty)
    ]), shot_id="shot_002"))
    upstream_missing_tail = [([0,0,1,1,1], {"tail": True}), ([0,0,0,0,0], {}), ([0,0,0,1,1], {})]
    add(CaseSpec("video", 19, "Case 19：Shot2 起点缺 TailFrame，Scene2 自有视觉恢复", "shot", 4, 2, 0, cross_shots([
        cross_scene("S1C1", upstream_done), cross_scene("S1C2", upstream_missing_tail), cross_scene("S2C1", empty), cross_scene("S2C2", [([0,1,0,0,0], {})] + empty[1:])
    ]), shot_id="shot_002"))
    cutaway_tail = [([0,0,1,1,1], {"tail": True}), ([0,0,0,0,0], {}), ([0,0,0,1,1], {"tail": True, "talking_head": False})]
    add(CaseSpec("video", 20, "Case 20：上游为空镜，TailFrame 存在但不可继承", "shot", 4, 2, 0, cross_shots([
        cross_scene("S1C1", upstream_done), cross_scene("S1C2", cutaway_tail), cross_scene("S2C1", empty), cross_scene("S2C2", [([0,1,0,0,0], {})] + empty[1:])
    ]), shot_id="shot_002"))
    add(CaseSpec("video", 21, "Case 21：绑定终视频跨 Shot 作为上游尾帧来源", "task", 4, 2, 0, cross_shots([
        cross_scene("S1C1", [([0,0,0,0,1], {"tail": True})] + empty[1:]), cross_scene("S1C2", empty), cross_scene("S2C1", empty), cross_scene("S2C2", empty)
    ])))

    image_inputs = [
        ([0,1,0,0,0], False, {}, "只有原图，先生成 Image Prompt"),
        ([0,1,0,0,0], True, {}, "原图 + Prompt 存在，可生成新图"),
        ([0,0,1,0,0], False, {}, "新图已存在，Prompt 不需要补"),
        ([0,0,0,0,0], True, {}, "Prompt 存在但没有原图"),
        ([0,1,0,1,0], False, {}, "Raw 已存在，新图缺失也不补"),
        ([0,0,0,0,1], True, {}, "Final 已存在，Prompt 存在仍绿"),
        ([1,1,0,0,0], False, {}, "音频不影响 Image Plan"),
        ([0,1,0,0,0], True, {"image": "running"}, "新图执行中"),
        ([0,1,0,0,0], True, {"image": "failed"}, "新图执行失败"),
    ]
    for idx, (slots, p, exe, title) in enumerate(image_inputs, start=1):
        dlg = ds(5, 2, slots=slots)
        dlg[0].image_prompt = p
        add(CaseSpec("image", idx, f"Case {idx}：{title}", "scene", 15, 2, 0, one_scene(dlg), execution=exe))
    add(CaseSpec("image", 10, "Case 10：跨 Shot 完整 Image Plan，Prompt 控制新图", "task", 4, 2, 0, cross_shots([
        cross_scene("S1C1", [([0,1,0,0,0], {"image_prompt": True})] + empty[1:]), cross_scene("S1C2", empty), cross_scene("S2C1", empty), cross_scene("S2C2", empty)
    ])))
    add(CaseSpec("image", 11, "Case 11：跨 Shot 下游 Raw/Final 已存在，Image Plan 不补图", "task", 4, 2, 0, cross_shots([
        cross_scene("S1C1", [([0,0,1,1,1], {}), ([0,0,0,0,0], {}), ([0,0,0,1,1], {})]),
        cross_scene("S1C2", [([0,0,0,1,0], {}), ([0,0,0,0,0], {}), ([0,0,0,0,1], {})]),
        cross_scene("S2C1", [([0,1,0,1,0], {}), ([0,0,0,0,0], {}), ([0,1,0,0,1], {})]),
        cross_scene("S2C2", [([0,0,1,0,0], {}), ([0,0,0,0,0], {}), ([0,1,0,0,0], {})]),
    ])))

    vop_inputs = [
        ([0,1,0,0,0], False, {}, "只有原图，Video Only Plan 先补新图"),
        ([0,0,1,0,0], False, {}, "新图存在但 Prompt 不存在"),
        ([0,0,1,0,0], True, {}, "新图 + Prompt 存在，可生成 Raw"),
        ([0,0,0,1,0], False, {}, "Raw 存在，Confirm Final 待执行"),
        ([0,0,0,0,1], False, {}, "Final 存在，Raw 缺失不反向点亮"),
        ([1,0,0,1,1], False, {}, "Raw + Final 都存在"),
        ([0,0,0,1,0], False, {}, "音频不影响 Confirm Final"),
        ([0,0,1,0,0], True, {"raw": "running"}, "Raw 生成运行中"),
        ([0,0,1,0,0], True, {"raw": "failed"}, "Raw 生成失败"),
    ]
    for idx, (slots, p, exe, title) in enumerate(vop_inputs, start=1):
        dlg = ds(5, 2, slots=slots)
        dlg[0].video_prompt = p
        add(CaseSpec("video_only", idx, f"Case {idx}：{title}", "scene", 15, 2, 0, one_scene(dlg), execution=exe))
    add(CaseSpec("video_only", 10, "Case 10：跨 Shot 完整 Video Only Plan，Prompt 控制 Raw", "task", 4, 2, 0, cross_shots([
        cross_scene("S1C1", [([0,0,1,0,0], {"video_prompt": True})] + empty[1:]), cross_scene("S1C2", empty), cross_scene("S2C1", empty), cross_scene("S2C2", empty)
    ])))
    add(CaseSpec("video_only", 11, "Case 11：跨 Shot Raw 已存在，可逐段 Confirm Final", "task", 4, 2, 0, cross_shots([
        cross_scene("S1C1", [([0,0,1,1,1], {}), ([0,0,0,0,0], {}), ([0,0,0,1,1], {})]),
        cross_scene("S1C2", [([0,0,0,1,1], {}), ([0,0,0,0,0], {}), ([0,0,0,1,1], {})]),
        cross_scene("S2C1", [([0,0,0,1,0], {}), ([0,0,0,0,0], {}), ([0,0,0,1,0], {})]),
        cross_scene("S2C2", [([0,0,0,1,0], {}), ([0,0,0,0,0], {}), ([0,0,0,1,0], {})]),
    ])))
    return out


def inject_screenshot_sections() -> None:
    mapping = {
        "video": ("koubo_segment_tailframe_exhaustive_matrix.html", 21),
        "image": ("koubo_image_plan_test_matrix.html", 11),
        "video_only": ("koubo_video_only_plan_test_matrix.html", 11),
    }
    for suite, (filename, count) in mapping.items():
        path = DOC_DIR / filename
        text = path.read_text(encoding="utf-8")
        marker = f"koubo-plan-actual-{suite}"
        block = f"""
  <script id="{marker}">
    (() => {{
      const shots = {json.dumps([f"koubo_plan_case_actual_results/{suite}_case_{i:02d}.png" for i in range(1, count + 1)], ensure_ascii=False)};
      const attach = () => {{
        document.querySelectorAll(".actual-shot").forEach((node) => node.remove());
        document.querySelectorAll(".compare-case").forEach((node, index) => {{
          const src = shots[index];
          if (!src) return;
          const section = document.createElement("div");
          section.className = "actual-shot";
          section.innerHTML = `<div class="side-head"><span>实测截图</span><span>Case ${{String(index + 1).padStart(2, "0")}}</span></div><img src="${{src}}" alt="实测截图 Case ${{index + 1}}">`;
          node.appendChild(section);
        }});
      }};
      if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => requestAnimationFrame(attach));
      else requestAnimationFrame(attach);
    }})();
  </script>
"""
        css = """
    .actual-shot { border-top: 1px solid #d4deec; padding: 14px; background: #fff; }
    .actual-shot img { display: block; width: 100%; border: 1px solid #d4deec; border-radius: 8px; background: #fff; }
"""
        if ".actual-shot img" not in text:
            text = text.replace("</style>", css + "\n  </style>", 1)
        start = text.find(f'<script id="{marker}">')
        if start >= 0:
            end = text.find("</script>", start)
            text = text[:start] + block + text[end + len("</script>"):]
        else:
            text = text.replace("</body>", block + "\n</body>", 1)
        path.write_text(text, encoding="utf-8")


def main() -> int:
    if not WORKSPACE.exists():
        raise SystemExit(f"workspace not found: {WORKSPACE}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    backup = Path("/private/tmp") / f"koubo_task4_session5_before_43_case_run_{now_ms()}"
    shutil.copytree(WORKSPACE / "SessionOutput/storyboard", backup / "SessionOutput/storyboard", dirs_exist_ok=True)
    if (WORKSPACE / "S7_04_02_StoryBoard").exists():
        shutil.copytree(WORKSPACE / "S7_04_02_StoryBoard", backup / "S7_04_02_StoryBoard", dirs_exist_ok=True)
    all_cases = cases()
    results = []
    manifest = {"backup": str(backup), "cases": []}
    for index, case in enumerate(all_cases, start=1):
        print(f"[{index:02d}/{len(all_cases)}] {case.suite} case {case.number}: {case.title}", flush=True)
        result = run_case(case)
        results.append(result)
        plan = result.get("plan") or {}
        manifest["cases"].append({
            "suite": case.suite,
            "case": case.number,
            "title": case.title,
            "target_type": case.target_type,
            "tool_status": (result.get("tool_result") or {}).get("status"),
            "returncode": (result.get("tool_result") or {}).get("_returncode"),
            "summary": plan.get("summary") or {},
        })
    html_path = write_actual_html(results)
    write_json(OUT_DIR / "run_manifest.json", manifest)
    inject_screenshot_sections()
    print(f"ACTUAL_HTML={html_path}")
    print(f"MANIFEST={OUT_DIR / 'run_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
