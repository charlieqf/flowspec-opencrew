from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


TOOL_ID = "05_01_Scene_ScenePromptRefresh"
TOOL_NAME = "Scene Prompt Refresh"
TOOL_VERSION = "1.0.0"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
REQUIRES = ["rebuild_shot_plan.json", "source_package.json", "confirmed_first_last", "task_id", "opencode_session_context", "run_model", "shot_id", "scene_mark_id"]
PRODUCES = ["rebuild_shot_plan.json", f"reports/rebuild_v1/{TOOL_ID}.json"]
SUGGESTED_PREVIOUS_TOOLS = ["04_03_ShotPlan_FirstLastReadinessCheck"]
SUGGESTED_NEXT_TOOLS: list[str] = []


class ToolError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def resolve_database_url(args: argparse.Namespace) -> str:
    env_name = str(getattr(args, "database_url_env", "") or DEFAULT_DATABASE_URL_ENV)
    explicit = str(getattr(args, "database_url", "") or "")
    return explicit or os.environ.get(env_name) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL


def redacted_database_url(database_url: str) -> str:
    parsed = urllib.parse.urlsplit(normalize_database_url(database_url))
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    user = parsed.username or ""
    auth = f"{user}:***@" if user else ""
    return urllib.parse.urlunsplit((parsed.scheme, f"{auth}{host}{port}", parsed.path, parsed.query, parsed.fragment))


def postgres_connect(database_url: str) -> Any:
    try:
        import psycopg  # type: ignore
        conn = psycopg.connect(normalize_database_url(database_url))
        conn.execute("SET client_encoding TO 'UTF8'")
        return conn
    except Exception:
        try:
            import psycopg2  # type: ignore
        except Exception as exc:
            raise ToolError("PostgreSQL driver is not available. Install psycopg[binary] or psycopg2-binary in the OpenCrew runtime.") from exc
        conn = psycopg2.connect(normalize_database_url(database_url))
        conn.set_client_encoding("UTF8")
        return conn


def get_setting(conn: Any, key: str) -> Any:
    with conn.cursor() as cursor:
        cursor.execute("SELECT value FROM app_settings WHERE key = %s LIMIT 1", (key,))
        row = cursor.fetchone()
    if not row:
        return None
    try:
        return json.loads(decode_text(row[0]))
    except Exception:
        return None


def fetch_rebuild_context(database_url: str, task_id: int) -> dict[str, Any]:
    if not task_id:
        raise ToolError("05_01 requires --task-id so it can load the OC-Rebuild run model and OpenCode session context")
    conn = postgres_connect(database_url)
    try:
        base_url = str(get_setting(conn, "opencode.base_url") or "").strip().rstrip("/")
        username = str(get_setting(conn, "opencode.username") or "").strip()
        password = str(get_setting(conn, "opencode.password") or "").strip()
        with conn.cursor() as cursor:
            cursor.execute(
                """
SELECT t.id, t.session_id, t.analysis_task_id, a.session_id AS analysis_session_id,
       t.source_package_path, t.final_prompt, t.run_model_provider, t.run_model_id,
       s.workspace_dir, s.opencode_session_id
FROM oc_rebuild_tasks t
JOIN sessions s ON s.id = t.session_id
LEFT JOIN openclip_tasks a ON a.id = t.analysis_task_id
WHERE t.id = %s
LIMIT 1
""",
                (task_id,),
            )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
        if not row:
            raise ToolError(f"OC-Rebuild Task #{task_id} not found")
        data = dict(zip(columns, row))
        final_prompt = decode_text(data.get("final_prompt")).strip()
        provider = decode_text(data.get("run_model_provider")).strip()
        model_id = decode_text(data.get("run_model_id")).strip()
        opencode_session_id = decode_text(data.get("opencode_session_id")).strip()
        if not final_prompt:
            raise ToolError(f"OC-Rebuild Task #{task_id} has no Final Prompt")
        if not provider or not model_id:
            raise ToolError(f"OC-Rebuild Task #{task_id} has no run model configured")
        if not base_url or not username or not password or not opencode_session_id:
            raise ToolError("OpenCode connection or bound session is incomplete")
        return {
            "task_id": int(data.get("id") or 0),
            "session_id": int(data.get("session_id") or 0),
            "analysis_task_id": int(data.get("analysis_task_id") or 0) or None,
            "analysis_session_id": int(data.get("analysis_session_id") or 0) or None,
            "workspace_dir": decode_text(data.get("workspace_dir")).strip(),
            "source_package_path": decode_text(data.get("source_package_path")).strip() or "source_package.json",
            "final_prompt": final_prompt,
            "run_model_provider": provider,
            "run_model_id": model_id,
            "opencode_session_id": opencode_session_id,
            "opencode_base_url": base_url,
            "opencode_username": username,
            "opencode_password": password,
            "database_url": database_url,
        }
    finally:
        conn.close()


def validate_rebuild_context_for_workspace(workspace: Path, plan: dict[str, Any], context: dict[str, Any]) -> None:
    context_workspace = Path(str(context.get("workspace_dir") or "")).expanduser()
    if context_workspace:
        context_workspace = context_workspace.resolve()
    requested_workspace = workspace.expanduser().resolve()
    if context_workspace and context_workspace != requested_workspace:
        raise ToolError(
            "OC-Rebuild Task context points to a different workspace. "
            f"database={redacted_database_url(str(context.get('database_url') or ''))}, "
            f"task_workspace={context_workspace}, requested_workspace={requested_workspace}."
        )
    plan_task = plan.get("task") if isinstance(plan.get("task"), dict) else {}
    plan_task_id = int(safe_float(plan_task.get("task_id"), 0.0))
    plan_session_id = int(safe_float(plan_task.get("session_id"), 0.0))
    if plan_task_id and plan_task_id != int(context.get("task_id") or 0):
        raise ToolError(f"rebuild_shot_plan.json task_id={plan_task_id} does not match DB task_id={context.get('task_id')}")
    if plan_session_id and plan_session_id != int(context.get("session_id") or 0):
        raise ToolError(f"rebuild_shot_plan.json session_id={plan_session_id} does not match DB session_id={context.get('session_id')}")
    model = plan.get("model") if isinstance(plan.get("model"), dict) else {}
    plan_provider = str(model.get("provider") or "").strip()
    plan_model = str(model.get("model") or "").strip()
    if plan_provider and plan_model and (plan_provider != context["run_model_provider"] or plan_model != context["run_model_id"]):
        raise ToolError(f"rebuild_shot_plan.json model does not match DB run model: plan={plan_provider}/{plan_model}, db={context['run_model_provider']}/{context['run_model_id']}")


def request_opencode_json(context: dict[str, Any], method: str, path: str, payload: dict[str, Any] | None = None, query: dict[str, str] | None = None, timeout: int = 120) -> Any:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    token = base64.b64encode(f"{context['opencode_username']}:{context['opencode_password']}".encode("utf-8")).decode("ascii")
    query_string = f"?{urllib.parse.urlencode(query)}" if query else ""
    req = urllib.request.Request(
        f"{context['opencode_base_url']}{path}{query_string}",
        data=data,
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:3000]
        raise ToolError(f"OpenCode {method} {path} failed: HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise ToolError(f"OpenCode {method} {path} failed: {exc}") from exc
    return json.loads(raw) if raw else None


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
        if message_role(message) != "user" or message_created_at(message) < started_at:
            continue
        text = message_text(message)
        if text == expected or (expected and text.startswith(expected[:2000])):
            return message_id(message)
    return ""


def assistant_text_for_parent(messages: list[dict[str, Any]], parent_id: str) -> str:
    if not parent_id:
        return ""
    for message in reversed(messages):
        if message_role(message) == "assistant" and message_parent_id(message) == parent_id:
            text = message_text(message)
            if text:
                return text
    return ""


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I).strip()
        stripped = re.sub(r"\s*```$", "", stripped).strip()
    start = stripped.find("{")
    if start < 0:
        raise ToolError("Model response did not contain a JSON object")
    parsed, _ = json.JSONDecoder().raw_decode(stripped[start:])
    if not isinstance(parsed, dict):
        raise ToolError("Model response JSON must be an object")
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


def source_workspace_from_package(workspace: Path, source_package: dict[str, Any]) -> Path | None:
    for key in ("workspace", "workspace_dir", "source_workspace"):
        value = source_package.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser()
    session = source_package.get("session") if isinstance(source_package.get("session"), dict) else {}
    for key in ("workspace", "workspace_dir"):
        value = session.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser()
    return None


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("shots", []) if isinstance(item, dict)]


def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    return [item for item in reference.get("scene_marks", []) if isinstance(item, dict)]


def target_shot(plan: dict[str, Any], shot_id: str) -> dict[str, Any] | None:
    return next((shot for shot in shot_list(plan) if str(shot.get("shot_id") or "") == shot_id), None)


def target_scene(shot: dict[str, Any], scene_mark_id: str) -> dict[str, Any] | None:
    return next((mark for mark in scene_marks_for_shot(shot) if str(mark.get("scene_mark_id") or "") == scene_mark_id), None)


def scene_frame_paths(mark: dict[str, Any]) -> tuple[str, str]:
    keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
    single = str(keyframes.get("single") or "").strip()
    first = str(keyframes.get("first") or single).strip()
    last = str(keyframes.get("last") or single or first).strip()
    return first, last


def is_confirmed(mark: dict[str, Any]) -> bool:
    first, last = scene_frame_paths(mark)
    mark_status = mark.get("mark_status") if isinstance(mark.get("mark_status"), dict) else {}
    return bool(mark_status.get("first_last_confirmed") and first and last)


def parse_srt_entries(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    pattern = re.compile(r"(?:(\d+)\s*)?(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*([\s\S]*?)(?=\n\s*\n|\Z)")
    for match in pattern.finditer(text or ""):
        body = " ".join(line.strip() for line in match.group(4).splitlines() if line.strip())
        if body:
            entries.append({"start": srt_time(match.group(2)), "end": srt_time(match.group(3)), "text": body})
    return entries


def srt_time(value: str) -> float:
    hh, mm, rest = value.replace(",", ".").split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(rest)


def srt_candidates_for_range(srt_text: str, shot_start: float, start: float, end: float) -> list[dict[str, Any]]:
    entries = parse_srt_entries(srt_text)
    local_start = max(0.0, start - shot_start)
    local_end = max(local_start, end - shot_start)
    rows: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        block_start = safe_float(entry.get("start"))
        block_end = safe_float(entry.get("end"))
        overlap = max(0.0, min(local_end, block_end) - max(local_start, block_start))
        expanded_overlap = max(0.0, min(local_end + 2.0, block_end) - max(max(0.0, local_start - 2.0), block_start))
        if overlap <= 0 and expanded_overlap <= 0:
            continue
        rows.append({"index": index, "start": round(block_start, 3), "end": round(block_end, 3), "scene_local_start": round(local_start, 3), "scene_local_end": round(local_end, 3), "overlap_seconds": round(overlap, 3), "text": str(entry.get("text") or "").strip()})
    return rows[:8]


def compact_ocr_text(text: Any) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\b[A-Za-z0-9]{4,}\b", "", value)
    return re.sub(r"\s+", " ", value).strip()


def ocr_candidates_for_range(ocr_text: Any, start: float, end: float, first_path: str = "", last_path: str = "") -> list[dict[str, Any]]:
    if not isinstance(ocr_text, list):
        return []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(ocr_text, start=1):
        if not isinstance(item, dict):
            continue
        text = compact_ocr_text(item.get("text"))
        if not text:
            continue
        item_start = safe_float(item.get("start", item.get("time", start)))
        item_end = safe_float(item.get("end"), item_start)
        paths = [str(path or "") for path in (item.get("source_keyframe_paths") or [])]
        path_match = bool((first_path and first_path in paths) or (last_path and last_path in paths))
        overlap = max(0.0, min(end, item_end) - max(start, item_start))
        nearby_overlap = max(0.0, min(end + 2.0, item_end) - max(start - 2.0, item_start))
        if path_match or overlap > 0 or nearby_overlap > 0:
            rows.append({"index": index, "start": round(item_start, 3), "end": round(item_end, 3), "overlap_seconds": round(overlap, 3), "text": text, "raw_text": str(item.get("text") or "").strip(), "source_keyframe_paths": paths[:8]})
    return rows[:8]


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


def compact_keyframes(frames: Any) -> list[dict[str, Any]]:
    if not isinstance(frames, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for frame in sorted([item for item in frames if isinstance(item, dict)], key=lambda item: (safe_float(item.get("time"), 1_000_000), str(item.get("path") or ""))):
        path = str(frame.get("path") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        rows.append({"time": round(safe_float(frame.get("time")), 3), "path": path, "source": frame.get("source") or "", "role_hint": frame.get("role") or ""})
    return rows


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


def scene_mark_keyframes_for_refresh(keyframes: list[dict[str, Any]], scene_marks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path = {str(frame.get("path") or ""): frame for frame in compact_keyframes(keyframes)}
    selected: dict[str, dict[str, Any]] = {}
    for mark in scene_marks:
        mark_keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
        for path in (mark_keyframes.get("single"), mark_keyframes.get("first"), mark_keyframes.get("last")):
            path_value = str(path or "").strip()
            if path_value and path_value in by_path:
                selected[path_value] = by_path[path_value]
    return sorted(selected.values(), key=lambda item: (safe_float(item.get("time"), 1_000_000), str(item.get("path") or "")))


def build_scene_prompt_refresh_prompt(context: dict[str, Any], shot: dict[str, Any], keyframes: list[dict[str, Any]], scene_marks: list[dict[str, Any]]) -> str:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    shot_start = safe_float(shot.get("start"))
    srt_text = str(reference.get("srt_text") or "")
    ocr_text = reference.get("ocr_text") if isinstance(reference.get("ocr_text"), list) else []
    fixed_scene_marks: list[dict[str, Any]] = []
    for mark in scene_marks:
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
    return """你是 OpenClip 05_01 Scene Prompt Refresh 工具。

你需要先阅读随消息附带的首/尾帧图片，然后只刷新每个既有 Scene 的视频生成提示词和 Scene 级 SRT。Scene 边界、首尾帧路径、start、end、duration 已经由上游或用户确定，严禁修改。

核心优先级：
1. 提示词必须优先来自首/尾帧图片本身的视觉理解，包括主体、动作、构图、道具、场景、光线、镜头距离和画面变化。
2. SRT、ui_summary、rebuild_direction、generation_hint 只能作为语义匹配和补充，不能覆盖图片中不存在的主要画面。
3. 如果字幕语义和图片不一致，以图片为准。
4. 每个 Scene 会提供 srt_candidates 和 ocr_candidates。若 ocr_candidates 中存在画面字幕，必须优先用 OCR 定位当前 Scene 对应旁白，再和 srt_candidates / shot.srt_text 比对，输出接近原视频字幕的 srt_text。
5. srt_text 的目标是“原字幕连续片段”，不是关键词、摘要、标签或改写后的概念词。必须优先保持原词、原语序、原句式。
6. 包装文字、品牌、水印、账号、乱码、规格参数属于 visual text，不要当作旁白 srt_text。
7. 健康养生内容不得医疗化、不得承诺疗效、不得夸大。
8. 不得返回新的 keyframe path、start、end 或 duration。

严格要求：只输出 JSON 对象，不要解释，不要 Markdown。scenes 数量和 scene_mark_id 必须与 fixed_scene_marks 一致。

输出 JSON 结构：
{
  "shot_id": "shot_002",
  "scenes": [
    {
      "scene_mark_id": "shot_002_scene_001",
      "srt_text": "",
      "srt_source_span": "",
      "srt_is_verbatim_or_near_verbatim": true,
      "srt_edit_reason": "",
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


def call_scene_prompt_refresh_model(context: dict[str, Any], shot: dict[str, Any], keyframes: list[dict[str, Any]], scene_marks: list[dict[str, Any]], image_workspace: Path, timeout_seconds: int) -> dict[str, Any]:
    started_at = now_ms()
    image_parts: list[dict[str, Any]] = []
    missing_images: list[str] = []
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
        raise ToolError(f"No readable scene mark images for {shot.get('shot_id')}: {missing_images}")
    prompt_text = build_scene_prompt_refresh_prompt(context, shot, keyframes, scene_marks)
    request_opencode_json(
        context,
        "POST",
        f"/session/{context['opencode_session_id']}/prompt_async",
        {"parts": [{"type": "text", "text": prompt_text}] + image_parts, "model": {"providerID": context["run_model_provider"], "modelID": context["run_model_id"]}},
        query={"directory": context["workspace_dir"]},
        timeout=30,
    )
    deadline = time.time() + timeout_seconds
    response_text = ""
    parent_id = ""
    while time.time() < deadline:
        messages = request_opencode_json(context, "GET", f"/session/{context['opencode_session_id']}/message", None, query={"directory": context["workspace_dir"], "limit": "160"}, timeout=30) or []
        parent_id = parent_id or matching_user_prompt_id(messages, started_at, prompt_text)
        response_text = assistant_text_for_parent(messages, parent_id)
        if response_text:
            break
        time.sleep(1)
    if not response_text:
        raise ToolError(f"OpenCode timed out before refreshing scene prompts for {shot.get('shot_id')}")
    return extract_json_object(response_text)


def refresh_scene(context: dict[str, Any], source_package: dict[str, Any], shot: dict[str, Any], mark: dict[str, Any], image_workspace: Path, timeout_seconds: int) -> dict[str, Any]:
    if not is_confirmed(mark):
        return {"shot_id": shot.get("shot_id"), "scene_mark_id": mark.get("scene_mark_id"), "status": "blocked", "warnings": ["first_last_not_confirmed"]}
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    keyframes = reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else []
    if not keyframes:
        return {"shot_id": shot.get("shot_id"), "scene_mark_id": mark.get("scene_mark_id"), "status": "skipped", "message": "No keyframes"}
    attach_source_ocr_to_shot(shot, source_package)
    valid_marks = [mark]
    refresh_keyframes = scene_mark_keyframes_for_refresh(keyframes, valid_marks)
    if not refresh_keyframes:
        raise ToolError(f"{shot.get('shot_id')} / {mark.get('scene_mark_id')} has no readable first/last keyframes to refresh")
    payload = call_scene_prompt_refresh_model(context, shot, refresh_keyframes, valid_marks, image_workspace, timeout_seconds)
    returned = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
    raw_scene = next((item for item in returned if isinstance(item, dict) and str(item.get("scene_mark_id") or "") == str(mark.get("scene_mark_id") or "")), None)
    if not raw_scene and len(returned) == 1 and isinstance(returned[0], dict):
        raw_scene = returned[0]
    if not raw_scene:
        raise ToolError(f"Refresh model returned no matching scene for {shot.get('shot_id')} / {mark.get('scene_mark_id')}")
    matched_srt = str(raw_scene.get("srt_text") or "").strip()
    mark["srt_text"] = matched_srt or str(mark.get("srt_text") or "").strip()
    mark["srt_source_span"] = str(raw_scene.get("srt_source_span") or matched_srt or "").strip()
    mark["srt_is_verbatim_or_near_verbatim"] = bool(raw_scene.get("srt_is_verbatim_or_near_verbatim", True))
    mark["srt_edit_reason"] = str(raw_scene.get("srt_edit_reason") or "").strip()
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
    mark["prompt_source"] = "05_01_scene_prompt_refresh"
    mark["prompt_priority"] = "keyframe_image_first"
    mark["prompt_refreshed_at"] = now_ms()
    if isinstance(raw_scene.get("warnings"), list):
        mark["warnings"] = raw_scene["warnings"]
    return {"shot_id": shot.get("shot_id"), "scene_mark_id": mark.get("scene_mark_id"), "status": "completed", "mark_mode": "refresh_prompts", "srt_match_source": mark.get("srt_match_source"), "model": {"provider": context.get("run_model_provider"), "model": context.get("run_model_id")}}


def scope(args: argparse.Namespace) -> dict[str, Any]:
    return {"shot_id": args.shot_id[0] if args.shot_id else "", "scene_mark_id": args.scene_mark_id}


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    satisfied: list[Any] = []
    missing: list[dict[str, Any]] = []
    if len(args.shot_id) != 1:
        missing.append({"dependency": "shot_id", "reason": "scene-level tool requires exactly one --shot-id", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
    if not args.scene_mark_id:
        missing.append({"dependency": "scene_mark_id", "reason": "scene-level tool requires --scene-mark-id", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
    if not args.task_id:
        missing.append({"dependency": "task_id", "reason": "05_01 requires --task-id to load OC-Rebuild run model and OpenCode session context", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
    for name, path in (("rebuild_shot_plan.json", workspace / args.input), ("source_package.json", workspace / args.source_package)):
        if path.exists():
            satisfied.append(name)
        else:
            missing.append({"dependency": name, "reason": f"required workspace file does not exist: {path.name}", "suggested_tools": ["01_Rebuild_SourcePackageLoad" if name == "source_package.json" else "02_RebuildShotPlanBuilder"], "scope": scope(args)})
    if (workspace / args.input).exists() and len(args.shot_id) == 1 and args.scene_mark_id:
        plan = read_json(workspace / args.input)
        shot = target_shot(plan, args.shot_id[0])
        mark = target_scene(shot or {}, args.scene_mark_id) if shot else None
        if mark and is_confirmed(mark):
            satisfied.append("confirmed_first_last")
        else:
            missing.append({"dependency": "confirmed_first_last", "reason": "target scene is missing confirmed first/last frames", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
    if args.task_id:
        satisfied.extend(["task_id", "opencode_session_context", "run_model"])
    return {"status": "blocked" if missing else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": []}


def run(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    plan = read_json(workspace / args.input)
    source_package = read_json(workspace / args.source_package)
    context = fetch_rebuild_context(resolve_database_url(args), int(args.task_id))
    validate_rebuild_context_for_workspace(workspace, plan, context)
    image_workspace = source_workspace_from_package(workspace, source_package) or workspace
    shot = target_shot(plan, args.shot_id[0])
    if not shot:
        raise RuntimeError(f"Shot not found: {args.shot_id[0]}")
    mark = target_scene(shot, args.scene_mark_id)
    if not mark:
        raise RuntimeError(f"Scene mark not found: {args.scene_mark_id}")
    result = refresh_scene(context, source_package, shot, mark, image_workspace, int(args.timeout_seconds))
    write_json(workspace / args.output, plan)
    write_json(workspace / "reports" / "rebuild_v1" / f"{TOOL_ID}.json", {"status": result["status"], "calibration": "image_ocr_srt_aligned", "results": [result]})
    return {"status": "completed" if result["status"] == "completed" else "completed_with_blockers", "calibration": "image_ocr_srt_aligned", "results": [result]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--shot-id", action="append", default=[])
    parser.add_argument("--scene-mark-id", default="")
    parser.add_argument("--timeout-seconds", type=int, default=300)
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
