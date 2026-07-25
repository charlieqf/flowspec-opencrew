from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_NAME = "RebuildSingleShotAssetBuilder"
TOOL_VERSION = "0.1.0"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
DEFAULT_VARIANT_ID = "variant_001"
MODES = ("single", "first_last")
SCENE_SCOPES = ("all", "first", "one")
VALIDATE_MODES = ("none", "quick", "full")
DEFAULT_IMAGE_NEGATIVE_PROMPT = "text, subtitles, captions, watermark, logo, UI text, extra fingers, distorted face, plastic skin, low quality, blurry"
DEFAULT_VIDEO_NEGATIVE_PROMPT = "subtitles, captions, watermark, logo, UI text, flicker, jitter, distorted face, bad hands, low quality, unreadable text"
VIDEO_PROMPT_DOC_DIR = Path(__file__).with_name("video_prompt_docs")


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


def optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return read_json(path)


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


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_video_prompt_docs() -> dict[str, str]:
    docs: dict[str, str] = {}
    for provider in ("gemini", "openai", "xai", "wan"):
        path = VIDEO_PROMPT_DOC_DIR / f"{provider}.md"
        docs[provider] = path.read_text(encoding="utf-8")[:20000] if path.exists() and path.is_file() else ""
    return docs


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


def resolve_workspace_path(workspace: Path, path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else workspace / path


def image_file_part(path: Path, workspace: Path) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    try:
        filename = path.relative_to(workspace).as_posix()
    except ValueError:
        filename = path.name
    return {"type": "file", "mime": mime, "filename": filename, "url": f"data:{mime};base64,{encoded}"}


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


def output_dims(source_package: dict[str, Any]) -> tuple[int, int, int]:
    video = source_package.get("video") if isinstance(source_package.get("video"), dict) else {}
    width = safe_int(video.get("width"), 720) or 720
    height = safe_int(video.get("height"), 1280) or 1280
    fps = safe_int(video.get("fps"), 30) or 30
    return width, height, fps


def normalize_keyframes(keyframes: Any) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    if not isinstance(keyframes, list):
        return []
    for frame in keyframes:
        if not isinstance(frame, dict):
            continue
        path = str(frame.get("path") or "").strip()
        if path:
            by_path[path] = dict(frame)
    return sorted(by_path.values(), key=lambda item: (safe_float(item.get("time"), 1_000_000.0), str(item.get("path") or "")))


def compact_keyframe(frame: dict[str, Any] | None) -> dict[str, Any]:
    if not frame:
        return {"time": 0.0, "path": "", "source": ""}
    return {"time": safe_float(frame.get("time"), 0.0), "path": str(frame.get("path") or ""), "source": frame.get("source") or ""}


def variants_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    variants = [item for item in (plan.get("variants") or []) if isinstance(item, dict)]
    return variants or [{"variant_id": DEFAULT_VARIANT_ID}]


def find_shot(plan: dict[str, Any], shot_id: str) -> dict[str, Any]:
    shots = plan.get("shots") if isinstance(plan.get("shots"), list) else []
    shot = next((item for item in shots if isinstance(item, dict) and str(item.get("shot_id") or "") == shot_id), None)
    if not shot:
        raise RuntimeError(f"Shot not found: {shot_id}")
    return shot


def find_scene_mark(shot: dict[str, Any], scene_mark_id: str) -> dict[str, Any] | None:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    marks = reference.get("scene_marks") if isinstance(reference.get("scene_marks"), list) else []
    if scene_mark_id:
        mark = next((item for item in marks if isinstance(item, dict) and str(item.get("scene_mark_id") or "") == scene_mark_id), None)
        if not mark:
            raise RuntimeError(f"Scene mark not found: {scene_mark_id}")
        return mark
    return next((item for item in marks if isinstance(item, dict)), None)


def scene_marks_for_scope(shot: dict[str, Any], scene_mark_id: str, scope: str) -> list[dict[str, Any] | None]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    marks = [item for item in (reference.get("scene_marks") if isinstance(reference.get("scene_marks"), list) else []) if isinstance(item, dict)]
    if scene_mark_id:
        return [find_scene_mark(shot, scene_mark_id)]
    if scope == "one":
        raise RuntimeError("--scene-scope one requires --scene-mark-id")
    if scope == "first":
        return [marks[0]] if marks else [None]
    return marks if marks else [None]


def frame_by_path(keyframes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(frame.get("path") or ""): frame for frame in keyframes if str(frame.get("path") or "")}


def frame_for_path(keyframes: list[dict[str, Any]], path: str) -> dict[str, Any] | None:
    return frame_by_path(keyframes).get(path)


def select_frames_for_scene(shot: dict[str, Any], mode: str, scene_mark: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    keyframes = normalize_keyframes(reference.get("keyframes"))
    if not keyframes:
        raise RuntimeError(f"{shot.get('shot_id')} has no keyframes")
    mark_keyframes = scene_mark.get("keyframes") if isinstance(scene_mark, dict) and isinstance(scene_mark.get("keyframes"), dict) else {}
    if mode == "single":
        path = str(mark_keyframes.get("single") or mark_keyframes.get("first") or "").strip()
        return frame_for_path(keyframes, path) or keyframes[0], None, scene_mark
    first_path = str(mark_keyframes.get("first") or "").strip()
    last_path = str(mark_keyframes.get("last") or "").strip()
    first = frame_for_path(keyframes, first_path) or keyframes[0]
    last = frame_for_path(keyframes, last_path) or keyframes[-1]
    if first.get("path") == last.get("path"):
        raise RuntimeError(f"{shot.get('shot_id')} first_last mode requires two different keyframes")
    if safe_float(first.get("time"), 0.0) > safe_float(last.get("time"), 0.0):
        first, last = last, first
    return first, last, scene_mark


def scene_generation_mode(scene_mark: dict[str, Any] | None, fallback_mode: str) -> str:
    if isinstance(scene_mark, dict):
        value = str(scene_mark.get("generation_mode") or scene_mark.get("asset_generation_mode") or "").strip()
        if value == "first_last":
            return "first_last"
        if value == "first_frame":
            return "single"
    return "first_last" if fallback_mode == "first_last" and isinstance(scene_mark, dict) and scene_mark.get("generation_mode") == "first_last" else "single"


def apply_generation_mode_to_scene_mark(scene_mark: dict[str, Any] | None, asset_mode: str) -> None:
    if not isinstance(scene_mark, dict):
        return
    generation_mode = "first_last" if asset_mode == "first_last" else "first_frame"
    scene_mark["generation_mode"] = generation_mode
    asset_state = scene_mark.get("asset_state") if isinstance(scene_mark.get("asset_state"), dict) else {}
    scene_asset = asset_state.get("scene_asset") if isinstance(asset_state.get("scene_asset"), dict) else {}
    scene_asset["uses_only_first_frame"] = generation_mode != "first_last"
    asset_state["scene_asset"] = scene_asset
    scene_mark["asset_state"] = asset_state


def text_or_default(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def build_prompt(context: dict[str, Any], shot: dict[str, Any], source_package: dict[str, Any], mode: str, first_frame: dict[str, Any], last_frame: dict[str, Any] | None, scene_mark: dict[str, Any] | None) -> str:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    scene_description = scene_mark.get("scene_description") if isinstance(scene_mark, dict) and isinstance(scene_mark.get("scene_description"), dict) else {}
    payload = {
        "task": {"task_id": context["task_id"], "session_id": context["session_id"], "analysis_task_id": context["analysis_task_id"]},
        "final_prompt": context["final_prompt"],
        "mode": mode,
        "source_video": source_package.get("video") or {},
        "shot": {
            "shot_id": shot.get("shot_id"),
            "source_segment_id": shot.get("source_segment_id"),
            "start": shot.get("start"),
            "end": shot.get("end"),
            "duration": shot.get("duration"),
            "role": shot.get("role"),
            "formula_slot": shot.get("formula_slot"),
            "srt_text": reference.get("srt_text") or "",
            "ui_summary": field_text(shot.get("ui_summary"), ("summary", "what_happens", "title")),
            "rebuild_direction": field_text(shot.get("rebuild_direction"), ("direction", "new_scene", "new_spoken_script")),
            "generation_hint": field_text(shot.get("generation_hint"), ("hint", "visual", "prompt", "motion")),
            "quality_notes": shot.get("quality_notes") or [],
        },
        "scene_mark": {
            "scene_mark_id": scene_mark.get("scene_mark_id") if isinstance(scene_mark, dict) else "",
            "start": scene_mark.get("start") if isinstance(scene_mark, dict) else shot.get("start"),
            "end": scene_mark.get("end") if isinstance(scene_mark, dict) else shot.get("end"),
            "duration": scene_mark.get("duration") if isinstance(scene_mark, dict) else shot.get("duration"),
            "srt_text": scene_mark.get("srt_text") if isinstance(scene_mark, dict) else reference.get("srt_text") or "",
            "summary": scene_description.get("summary") or "",
            "visual_change": scene_description.get("visual_change") or "",
            "motion_prompt": scene_description.get("motion_prompt") or "",
            "video_prompt": scene_description.get("video_prompt") or "",
        },
        "selected_frames": {"first": compact_keyframe(first_frame), "last": compact_keyframe(last_frame) if last_frame else None},
        "video_prompt_docs": load_video_prompt_docs(),
    }
    return """你是 OpenClip Rebuild 04_1 Single Shot Asset Builder。

你需要先阅读随消息附带的参考图片，再根据一个 shot 的 SRT、Final Prompt、shot plan 和原视频参考内容，生成“重新生成图片”和“重新生成视频”所需的高质量 prompt asset 包。

核心目标：
1. 保持原 shot / SRT 的表达意思、情绪和信息目的。
2. 不要照搬原图片的具体画面、人物、背景、道具或粗糙构图；新画面必须和原图片不一致。
3. 新画面要更真实、更精致、更有短视频商业质感，更能体现 SRT 要表达的意思。
4. 图片 prompt 用于重新生成 image；视频 prompt 用于基于新图片继续生成 video。
5. base image / video 不要生成字幕、logo、水印、UI 文字或屏幕大字；字幕后续由 overlay 处理。
6. 如果是健康、疾病、养生、医疗相关主题，不得承诺疗效，不得制造恐惧，不得夸大或医疗化。
7. prompt 必须以可拍摄的真实视觉为主：主体、场景、构图、光线、镜头、质感、动作、情绪、时间变化。

模式要求：
1. mode=single：输出一张 new image prompt，表达当前参考帧对应的 SRT 语义；视频 prompt 表达从这张新图开始的自然运动。
2. mode=first_last：分别输出 new first image prompt 和 new last image prompt；两张图必须属于同一个新视觉方案，首尾状态有清晰连续变化；视频 prompt 描述从新首帧到新尾帧的运动过程。
3. VEO prompt 必须参考输入上下文里的 video_prompt_docs.gemini。
4. Sora prompt 必须参考输入上下文里的 video_prompt_docs.openai。
5. Grok prompt 必须参考输入上下文里的 video_prompt_docs.xai。
6. Wan prompt 必须参考输入上下文里的 video_prompt_docs.wan。

严格要求：
1. 只输出 JSON 对象，不要解释，不要 Markdown。
2. 不得返回输入中不存在的 frame path。
3. prompt 里不要要求生成字幕、标题、logo、水印、屏幕文字。
4. image prompt 和 video prompt 都必须是新视觉，不得描述为“same as reference image”。
5. 输出字段必须完整，即使某些内容为空也要给空字符串。

输出 JSON 结构：
{
  "shot_id": "shot_003",
  "scene_mark_id": "",
  "mode": "single",
  "srt_text": "",
  "reference": {
    "single_frame": "keyframes/...jpg",
    "first_frame": "keyframes/...jpg",
    "last_frame": "keyframes/...jpg",
    "reference_frame_prompt": "",
    "first_reference_prompt": "",
    "last_reference_prompt": ""
  },
  "image_prompts": {
    "single_image_prompt": "",
    "first_image_prompt": "",
    "last_image_prompt": "",
    "image_negative_prompt": "text, subtitles, captions, watermark, logo"
  },
  "video_prompts": {
    "base_video_prompt": "",
    "transition_video_prompt": "",
    "veo_prompt": "",
    "wan_prompt": "",
    "sora_prompt": "",
    "grok_prompt": "",
    "video_negative_prompt": "subtitles, captions, watermark, logo"
  },
  "generation_intent": {
    "kept_meaning": "",
    "changed_visuals": "",
    "style_upgrade": "",
    "srt_alignment": "",
    "first_to_last_motion": ""
  },
  "validation": {"status": "passed", "warnings": []}
}

输入上下文：
""" + json.dumps(payload, ensure_ascii=False, indent=2)


def call_model(context: dict[str, Any], shot: dict[str, Any], source_package: dict[str, Any], mode: str, first_frame: dict[str, Any], last_frame: dict[str, Any] | None, scene_mark: dict[str, Any] | None, image_workspace: Path, timeout_seconds: int) -> dict[str, Any]:
    started_at = int(time.time() * 1000)
    frames = [first_frame] + ([last_frame] if last_frame else [])
    image_parts = []
    missing_images = []
    for frame in frames:
        if not frame:
            continue
        path_value = str(frame.get("path") or "")
        image_path = resolve_workspace_path(image_workspace, path_value)
        if image_path.exists() and image_path.is_file():
            image_parts.append(image_file_part(image_path, image_workspace))
        else:
            missing_images.append(path_value)
    if not image_parts:
        raise RuntimeError(f"No readable images for {shot.get('shot_id')}: {missing_images}")
    REBUILD_03.request_json(
        context,
        "POST",
        f"/session/{context['opencode_session_id']}/prompt_async",
        {
            "parts": [{"type": "text", "text": build_prompt(context, shot, source_package, mode, first_frame, last_frame, scene_mark)}] + image_parts,
            "model": {"providerID": context["run_model_provider"], "modelID": context["run_model_id"]},
        },
        query={"directory": context["workspace_dir"]},
        timeout=30,
    )
    deadline = time.time() + timeout_seconds
    response_text = ""
    while time.time() < deadline:
        messages = REBUILD_03.request_json(context, "GET", f"/session/{context['opencode_session_id']}/message", None, query={"directory": context["workspace_dir"], "limit": "160"}, timeout=30) or []
        response_text = REBUILD_03.assistant_text(messages, started_at)
        if response_text:
            break
        time.sleep(1)
    if not response_text:
        raise RuntimeError(f"OpenCode timed out before returning asset prompts for {shot.get('shot_id')}")
    return extract_json_object(response_text)


def normalize_prompt_package(raw: dict[str, Any], shot: dict[str, Any], mode: str, first_frame: dict[str, Any], last_frame: dict[str, Any] | None, scene_mark: dict[str, Any] | None) -> dict[str, Any]:
    reference = raw.get("reference") if isinstance(raw.get("reference"), dict) else {}
    image_prompts = raw.get("image_prompts") if isinstance(raw.get("image_prompts"), dict) else {}
    video_prompts = raw.get("video_prompts") if isinstance(raw.get("video_prompts"), dict) else {}
    generation_intent = raw.get("generation_intent") if isinstance(raw.get("generation_intent"), dict) else {}
    warnings = []
    shot_reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    scene_srt = scene_mark.get("srt_text") if isinstance(scene_mark, dict) else ""
    srt_text = text_or_default(raw.get("srt_text"), str(scene_srt or shot_reference.get("srt_text") or ""))
    first_path = str(first_frame.get("path") or "")
    last_path = str(last_frame.get("path") or "") if last_frame else ""
    allowed_paths = {first_path, last_path} - {""}
    returned_paths = {str(reference.get(key) or "").strip() for key in ("single_frame", "first_frame", "last_frame") if str(reference.get(key) or "").strip()}
    invalid_paths = sorted(returned_paths - allowed_paths)
    if invalid_paths:
        warnings.append(f"Model returned frame paths outside selected inputs: {invalid_paths}")
    normalized_reference = {
        "single_frame": first_path if mode == "single" else "",
        "first_frame": first_path,
        "last_frame": last_path,
        "reference_frame_prompt": str(reference.get("reference_frame_prompt") or "").strip(),
        "first_reference_prompt": str(reference.get("first_reference_prompt") or reference.get("reference_frame_prompt") or "").strip(),
        "last_reference_prompt": str(reference.get("last_reference_prompt") or "").strip(),
    }
    package = {
        "version": 1,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "shot_id": str(shot.get("shot_id") or ""),
        "scene_mark_id": str(scene_mark.get("scene_mark_id") or "") if isinstance(scene_mark, dict) else "",
        "mode": mode,
        "generation_mode": "first_last" if mode == "first_last" else "first_frame",
        "srt_text": srt_text,
        "reference": normalized_reference,
        "image_prompts": {
            "single_image_prompt": str(image_prompts.get("single_image_prompt") or image_prompts.get("first_image_prompt") or "").strip(),
            "first_image_prompt": str(image_prompts.get("first_image_prompt") or image_prompts.get("single_image_prompt") or "").strip(),
            "last_image_prompt": str(image_prompts.get("last_image_prompt") or "").strip(),
            "image_negative_prompt": str(image_prompts.get("image_negative_prompt") or DEFAULT_IMAGE_NEGATIVE_PROMPT).strip(),
        },
        "video_prompts": {
            "base_video_prompt": str(video_prompts.get("base_video_prompt") or video_prompts.get("transition_video_prompt") or "").strip(),
            "transition_video_prompt": str(video_prompts.get("transition_video_prompt") or video_prompts.get("base_video_prompt") or "").strip(),
            "veo_prompt": str(video_prompts.get("veo_prompt") or video_prompts.get("base_video_prompt") or video_prompts.get("transition_video_prompt") or "").strip(),
            "wan_prompt": str(video_prompts.get("wan_prompt") or video_prompts.get("base_video_prompt") or video_prompts.get("transition_video_prompt") or "").strip(),
            "sora_prompt": str(video_prompts.get("sora_prompt") or video_prompts.get("base_video_prompt") or video_prompts.get("transition_video_prompt") or "").strip(),
            "grok_prompt": str(video_prompts.get("grok_prompt") or video_prompts.get("base_video_prompt") or video_prompts.get("transition_video_prompt") or "").strip(),
            "video_negative_prompt": str(video_prompts.get("video_negative_prompt") or DEFAULT_VIDEO_NEGATIVE_PROMPT).strip(),
        },
        "generation_intent": {
            "kept_meaning": str(generation_intent.get("kept_meaning") or "").strip(),
            "changed_visuals": str(generation_intent.get("changed_visuals") or "").strip(),
            "style_upgrade": str(generation_intent.get("style_upgrade") or "").strip(),
            "srt_alignment": str(generation_intent.get("srt_alignment") or "").strip(),
            "first_to_last_motion": str(generation_intent.get("first_to_last_motion") or "").strip(),
        },
        "validation": {"status": "passed", "warnings": warnings},
    }
    if not package["image_prompts"]["first_image_prompt"]:
        warnings.append("Missing first/single image prompt")
    if mode == "first_last" and not package["image_prompts"]["last_image_prompt"]:
        warnings.append("Missing last image prompt")
    if not package["video_prompts"]["veo_prompt"] or not package["video_prompts"]["wan_prompt"] or not package["video_prompts"]["sora_prompt"] or not package["video_prompts"]["grok_prompt"]:
        warnings.append("Missing one or more model-specific video prompts")
    package["validation"]["status"] = "passed" if not warnings else "warning"
    return package


def task_payload(task_id: str, shot: dict[str, Any], variant_id: str, task_type: str, executor: str, depends_on: list[str], params: dict[str, Any], output: Any, input_payload: dict[str, Any] | None = None, workflow: str | None = None, scene_mark_id: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": task_id,
        "shot_id": shot["shot_id"],
        "scene_mark_id": scene_mark_id,
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


def build_prompt_asset_tasks(shot: dict[str, Any], prompt_package: dict[str, Any], variant_id: str, width: int, height: int, fps: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    shot_id = str(shot.get("shot_id") or "shot")
    scene_mark_id = str(prompt_package.get("scene_mark_id") or "")
    scene_token = scene_mark_id or shot_id
    generation_mode = str(prompt_package.get("generation_mode") or "first_frame")
    mode = "first_last" if generation_mode == "first_last" else "single"
    duration = max(safe_float(shot.get("duration"), 0.0), 0.5)
    image_prompts = prompt_package.get("image_prompts") if isinstance(prompt_package.get("image_prompts"), dict) else {}
    video_prompts = prompt_package.get("video_prompts") if isinstance(prompt_package.get("video_prompts"), dict) else {}
    reference = prompt_package.get("reference") if isinstance(prompt_package.get("reference"), dict) else {}
    image_negative = str(image_prompts.get("image_negative_prompt") or DEFAULT_IMAGE_NEGATIVE_PROMPT)
    video_negative = str(video_prompts.get("video_negative_prompt") or DEFAULT_VIDEO_NEGATIVE_PROMPT)
    base_dir = f"assets/{variant_id}/{shot_id}"
    tasks: list[dict[str, Any]] = []

    first_image_id = f"task_{variant_id}_{scene_token}_image_first"
    first_image_output = f"{base_dir}/{scene_token}_first.png"
    tasks.append(task_payload(first_image_id, shot, variant_id, "image_regenerate_first" if mode == "first_last" else "image_regenerate_single", "image_model", [], {"width": width, "height": height, "negative_prompt": image_negative}, first_image_output, {"reference_frame": reference.get("first_frame") or reference.get("single_frame") or "", "prompt": image_prompts.get("first_image_prompt") or image_prompts.get("single_image_prompt") or "", "srt_text": prompt_package.get("srt_text") or ""}, "rebuild_image_prompt", scene_mark_id))

    image_depends = [first_image_id]
    video_input: dict[str, Any] = {"first_image": first_image_output, "srt_text": prompt_package.get("srt_text") or "", "veo_prompt": video_prompts.get("veo_prompt") or "", "wan_prompt": video_prompts.get("wan_prompt") or "", "sora_prompt": video_prompts.get("sora_prompt") or "", "grok_prompt": video_prompts.get("grok_prompt") or ""}
    if mode == "first_last":
        last_image_id = f"task_{variant_id}_{scene_token}_image_last"
        last_image_output = f"{base_dir}/{scene_token}_last.png"
        tasks.append(task_payload(last_image_id, shot, variant_id, "image_regenerate_last", "image_model", [], {"width": width, "height": height, "negative_prompt": image_negative}, last_image_output, {"reference_frame": reference.get("last_frame") or "", "prompt": image_prompts.get("last_image_prompt") or "", "srt_text": prompt_package.get("srt_text") or ""}, "rebuild_image_prompt", scene_mark_id))
        image_depends.append(last_image_id)
        video_input["last_image"] = last_image_output

    video_id = f"task_{variant_id}_{scene_token}_video"
    video_output = f"{base_dir}/{scene_token}_base.mp4"
    tasks.append(task_payload(video_id, shot, variant_id, "first_last_image_to_video" if mode == "first_last" else "single_image_to_video", "video_model", image_depends, {"duration": round(duration, 3), "width": width, "height": height, "fps": 24, "negative_prompt": video_negative, "base_video_prompt": video_prompts.get("base_video_prompt") or "", "transition_video_prompt": video_prompts.get("transition_video_prompt") or ""}, video_output, video_input, "model_select_veo_wan_sora", scene_mark_id))

    overlay_id = f"task_{variant_id}_{scene_token}_overlay"
    overlay_output = f"{base_dir}/{scene_token}_overlay.webm"
    tasks.append(task_payload(overlay_id, shot, variant_id, "hyperframe_overlay", "hyperframes", [video_id], {"duration": round(duration, 3), "width": width, "height": height, "fps": fps, "format": "webm", "transparent": True, "srt_text": prompt_package.get("srt_text") or "", "subtitle_style": "bottom_bold_keyword_highlight"}, overlay_output, None, None, scene_mark_id))

    composite_id = f"task_{variant_id}_{scene_token}_composite"
    final_output = f"{base_dir}/{scene_token}_final.mp4"
    tasks.append(task_payload(composite_id, shot, variant_id, "video_composite", "ffmpeg", [video_id, overlay_id], {"duration": round(duration, 3), "width": width, "height": height, "fps": fps}, final_output, {"base_video": video_output, "overlay": overlay_output}, None, scene_mark_id))

    summary = {"variant_id": variant_id, "shot_id": shot_id, "scene_mark_id": scene_mark_id, "selected_mode": mode, "generation_mode": generation_mode, "prompt_package": prompt_package, "tasks": [item["task_id"] for item in tasks], "output": final_output}
    return tasks, summary


def empty_asset_tasks(plan: dict[str, Any], source_package: dict[str, Any], output_path: str, mode: str) -> dict[str, Any]:
    width, height, fps = output_dims(source_package)
    return {
        "version": 1,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"shot_plan_path": "rebuild_shot_plan.json", "source_package_path": "source_package.json", "mode": mode},
        "defaults": {"width": width, "height": height, "fps": fps, "asset_prompt_builder": "04_1"},
        "task": plan.get("task") or {},
        "variants": variants_from_plan(plan),
        "shots": [],
        "tasks": [],
        "validation": {"status": "passed", "warnings": []},
        "_output_path": output_path,
    }


def merge_asset_tasks(existing: dict[str, Any] | None, plan: dict[str, Any], source_package: dict[str, Any], shot_summary: dict[str, Any], tasks: list[dict[str, Any]], mode: str, output_name: str) -> dict[str, Any]:
    asset_tasks = dict(existing) if isinstance(existing, dict) else empty_asset_tasks(plan, source_package, output_name, mode)
    shot_id = str(shot_summary.get("shot_id") or "")
    scene_mark_id = str(shot_summary.get("scene_mark_id") or "")
    variant_id = str(shot_summary.get("variant_id") or DEFAULT_VARIANT_ID)
    asset_tasks["tool"] = TOOL_NAME
    asset_tasks["tool_version"] = TOOL_VERSION
    asset_tasks["generated_at"] = datetime.now(timezone.utc).isoformat()
    asset_tasks["variants"] = asset_tasks.get("variants") if isinstance(asset_tasks.get("variants"), list) else variants_from_plan(plan)
    old_tasks = [item for item in (asset_tasks.get("tasks") or []) if isinstance(item, dict)]
    asset_tasks["tasks"] = [item for item in old_tasks if not (str(item.get("variant_id") or "") == variant_id and str(item.get("shot_id") or "") == shot_id and str(item.get("scene_mark_id") or "") == scene_mark_id)] + tasks
    old_shots = [item for item in (asset_tasks.get("shots") or []) if isinstance(item, dict)]
    asset_tasks["shots"] = [item for item in old_shots if not (str(item.get("variant_id") or "") == variant_id and str(item.get("shot_id") or "") == shot_id and str(item.get("scene_mark_id") or "") == scene_mark_id)] + [shot_summary]
    warnings = []
    for item in asset_tasks["tasks"]:
        if not item.get("task_id") or not item.get("type") or "params" not in item or "output" not in item:
            warnings.append(f"Invalid task shape: {item.get('task_id')}")
    asset_tasks["validation"] = {"status": "passed" if not warnings else "warning", "warnings": warnings}
    asset_tasks.pop("_output_path", None)
    return asset_tasks


def validate_prompt_package(package: dict[str, Any], mode: str, selected_paths: set[str], image_workspace: Path, validate_mode: str) -> list[str]:
    if validate_mode == "none":
        return []
    warnings: list[str] = []
    reference = package.get("reference") if isinstance(package.get("reference"), dict) else {}
    image_prompts = package.get("image_prompts") if isinstance(package.get("image_prompts"), dict) else {}
    video_prompts = package.get("video_prompts") if isinstance(package.get("video_prompts"), dict) else {}
    returned_paths = {str(reference.get(key) or "").strip() for key in ("single_frame", "first_frame", "last_frame") if str(reference.get(key) or "").strip()}
    invalid_paths = sorted(returned_paths - selected_paths)
    if invalid_paths:
        warnings.append(f"returned frame paths outside selected inputs: {invalid_paths}")
    if not str(image_prompts.get("single_image_prompt") or image_prompts.get("first_image_prompt") or "").strip():
        warnings.append("missing single/first image prompt")
    if mode == "first_last" and not str(image_prompts.get("last_image_prompt") or "").strip():
        warnings.append("missing last image prompt")
    for key in ("veo_prompt", "wan_prompt", "sora_prompt", "grok_prompt"):
        if not str(video_prompts.get(key) or "").strip():
            warnings.append(f"missing {key}")
    if validate_mode == "full":
        for path in sorted(selected_paths):
            if path and not resolve_workspace_path(image_workspace, path).is_file():
                warnings.append(f"selected image is not readable: {path}")
    return warnings


def apply_validation(package: dict[str, Any], warnings: list[str], validate_mode: str) -> None:
    existing = package.get("validation") if isinstance(package.get("validation"), dict) else {}
    merged_warnings = [str(item) for item in (existing.get("warnings") or []) if str(item).strip()] + warnings
    seen: set[str] = set()
    deduped = []
    for warning in merged_warnings:
        if warning not in seen:
            seen.add(warning)
            deduped.append(warning)
    package["validation"] = {"mode": validate_mode, "status": "passed" if not deduped else "warning", "warnings": deduped}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build single-shot image/video prompt assets using the OC-Rebuild Task run model.")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", required=True, type=int)
    parser.add_argument("--shot-id", required=True)
    parser.add_argument("--scene-mark-id", default="")
    parser.add_argument("--scene-scope", choices=SCENE_SCOPES, default="all", help="all processes every scene mark in the shot, first processes the first scene only, one requires --scene-mark-id.")
    parser.add_argument("--mode", choices=MODES, default="first_last")
    parser.add_argument("--validate", choices=VALIDATE_MODES, default="quick")
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--output", default="asset_tasks.json")
    parser.add_argument("--sidecar-output", default="")
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
        shot = find_shot(plan, args.shot_id)
        shot.setdefault("shot_id", args.shot_id)
        width, height, fps = output_dims(source_package)
        variant = variants_from_plan(plan)[0]
        variant_id = str(variant.get("variant_id") or DEFAULT_VARIANT_ID).strip() or DEFAULT_VARIANT_ID
        prompt_packages: list[dict[str, Any]] = []
        all_tasks: list[dict[str, Any]] = []
        shot_summaries: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        merged = optional_json(workspace / args.output)
        merged_asset_tasks = merged if isinstance(merged, dict) else None
        for scene_mark in scene_marks_for_scope(shot, str(args.scene_mark_id or ""), str(args.scene_scope)):
            asset_mode = scene_generation_mode(scene_mark, str(args.mode))
            apply_generation_mode_to_scene_mark(scene_mark, asset_mode)
            first_frame, last_frame, scene_mark = select_frames_for_scene(shot, asset_mode, scene_mark)
            if not first_frame:
                raise RuntimeError(f"No selected first frame for {args.shot_id}")
            raw = call_model(context, shot, source_package, asset_mode, first_frame, last_frame, scene_mark, image_workspace, int(args.timeout_seconds))
            prompt_package = normalize_prompt_package(raw, shot, asset_mode, first_frame, last_frame, scene_mark)
            selected_paths = {str(first_frame.get("path") or "")} | ({str(last_frame.get("path") or "")} if last_frame else set())
            apply_validation(prompt_package, validate_prompt_package(prompt_package, asset_mode, selected_paths - {""}, image_workspace, str(args.validate)), str(args.validate))
            tasks, shot_summary = build_prompt_asset_tasks(shot, prompt_package, variant_id, width, height, fps)
            merged_asset_tasks = merge_asset_tasks(merged_asset_tasks, plan, source_package, shot_summary, tasks, asset_mode, str(args.output))
            prompt_packages.append(prompt_package)
            all_tasks.extend(tasks)
            shot_summaries.append(shot_summary)
            results.append({"scene_mark_id": prompt_package.get("scene_mark_id") or "", "generation_mode": prompt_package.get("generation_mode") or "first_frame", "status": (prompt_package.get("validation") or {}).get("status") or "passed", "warning_count": len((prompt_package.get("validation") or {}).get("warnings") or []), "task_count": len(tasks)})
        sidecar_name = args.sidecar_output or f"asset_prompts_{args.shot_id}.json"
        sidecar_payload: Any = prompt_packages[0] if len(prompt_packages) == 1 else {"version": 1, "tool": TOOL_NAME, "tool_version": TOOL_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), "shot_id": args.shot_id, "mode": args.mode, "scene_scope": args.scene_scope, "validate": args.validate, "scenes": prompt_packages, "validation": {"status": "passed" if not any((pkg.get("validation") or {}).get("warnings") for pkg in prompt_packages) else "warning", "warnings": [warning for pkg in prompt_packages for warning in ((pkg.get("validation") or {}).get("warnings") or [])]}}
        write_json(workspace / sidecar_name, sidecar_payload)
        write_json(workspace / args.output, merged_asset_tasks or empty_asset_tasks(plan, source_package, str(args.output), str(args.mode)))
        result = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "completed", "mode": args.mode, "scene_scope": args.scene_scope, "validate": args.validate, "shot_id": args.shot_id, "scene_count": len(prompt_packages), "output": args.output, "sidecar_output": sidecar_name, "task_count": len(all_tasks), "warning_count": sum(item["warning_count"] for item in results), "results": results}
    except Exception as exc:
        result = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "failed", "message": str(exc)}
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
