from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOOL_NAME = "SegmentDescriptorSubtitleBuilder"
TOOL_VERSION = "0.2.0"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
RESTAURANT_TERMS = ["餐饮", "宴会", "后厨", "厨房", "厨师", "炒菜", "上菜", "餐具", "菜品", "保温柜", "前厅后厨"]
HEALTH_MANAGEMENT_TERMS = ["减肥", "减脂", "瘦", "体重", "肥胖", "身体管理", "健康管理", "忌口", "到店顾虑", "摩可多"]

SCHEMES = ["detail", "balanced", "summary"]
RETAKE_FIELD_KEYS = [
    "guide",
    "summary",
    "video_structure",
    "shooting_method",
    "camera",
    "shot_type",
    "visual_content",
    "scene",
    "main_scene",
    "people_coordination",
    "performer",
    "character_profile",
    "props",
    "emotion",
    "emotion_trigger",
    "spoken_script",
    "target_audience",
    "content_highlights",
    "product_or_business_focus",
    "main_action",
    "camera_movement",
    "composition",
    "transition_type",
    "editing_notes",
    "retake_notes",
    "visual_must_have",
]


class DependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Paths:
    workspace: Path | None
    meta_dir: Path
    transcripts_dir: Path


@dataclass(frozen=True)
class OpenCodeConfig:
    base_url: str
    username: str
    password: str
    directory: str
    session_id: str
    model: dict[str, str]
    task_id: int
    opencrew_session_id: int
    final_prompt: str


@dataclass(frozen=True)
class BusinessContext:
    scene: str
    performer: str
    character_profile: str
    props: str
    target_audience: str
    product_focus: str
    people_coordination: str
    domain_label: str


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def postgres_connect(database_url: str) -> Any:
    try:
        import psycopg  # type: ignore
    except Exception as exc:
        raise RuntimeError("PostgreSQL driver is not available. Install psycopg[binary] in the OpenCrew runtime.") from exc
    return psycopg.connect(normalize_database_url(database_url))


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


def fetch_opencode_config(database_url: str, task_id: int) -> OpenCodeConfig:
    conn = postgres_connect(database_url)
    try:
        base_url = str(get_setting(conn, "opencode.base_url") or "").strip().rstrip("/")
        username = str(get_setting(conn, "opencode.username") or "").strip()
        password = str(get_setting(conn, "opencode.password") or "").strip()
        if not base_url or not username or not password:
            raise RuntimeError("OpenCode connection is incomplete in app_settings")
        with conn.cursor() as cursor:
            cursor.execute(
                """
SELECT t.id, t.final_prompt, t.run_model_provider, t.run_model_id,
       s.id AS opencrew_session_id, s.opencode_session_id, s.workspace_dir
FROM openclip_tasks t
JOIN sessions s ON s.id = t.session_id
WHERE t.id = %s
LIMIT 1
""",
                (task_id,),
            )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
        if not row:
            raise RuntimeError(f"OpenClip Task #{task_id} not found")
        data = dict(zip(columns, row))
        final_prompt = decode_text(data.get("final_prompt")).strip()
        if not final_prompt:
            raise RuntimeError(f"Task #{task_id} has no final_prompt")
        session_id = decode_text(data.get("opencode_session_id")).strip()
        if not session_id:
            raise RuntimeError(f"Task #{task_id} has no bound OpenCode session")
        provider = decode_text(data.get("run_model_provider")).strip()
        model_id = decode_text(data.get("run_model_id")).strip()
        if not provider or not model_id:
            raise RuntimeError(f"Task #{task_id} has no run model configured")
        return OpenCodeConfig(
            base_url=base_url,
            username=username,
            password=password,
            directory=decode_text(data.get("workspace_dir")).strip(),
            session_id=session_id,
            model={"providerID": provider, "modelID": model_id},
            task_id=task_id,
            opencrew_session_id=int(data.get("opencrew_session_id") or 0),
            final_prompt=final_prompt,
        )
    finally:
        conn.close()


def fetch_task_final_prompt(database_url: str, task_id: int) -> str:
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT final_prompt FROM openclip_tasks WHERE id = %s LIMIT 1", (task_id,))
            row = cursor.fetchone()
        if not row:
            raise RuntimeError(f"OpenClip Task #{task_id} not found")
        final_prompt = decode_text(row[0]).strip()
        if not final_prompt:
            raise RuntimeError(f"Task #{task_id} has no final_prompt")
        return final_prompt
    finally:
        conn.close()


def request_json(config: OpenCodeConfig, method: str, path: str, payload: dict[str, Any] | None = None, query: dict[str, str] | None = None, timeout: int = 120) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    token = base64.b64encode(f"{config.username}:{config.password}".encode("utf-8")).decode("ascii")
    query_string = f"?{urlencode(query)}" if query else ""
    req = Request(
        f"{config.base_url}{path}{query_string}",
        data=data,
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    with urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    return json.loads(raw) if raw else None


def ensure_model_supports_image(config: OpenCodeConfig) -> None:
    payload = request_json(config, "GET", "/provider", None, query={"directory": config.directory}, timeout=30) or {}
    connected = {str(item) for item in (payload.get("connected") or []) if item}
    provider_id = config.model["providerID"]
    model_id = config.model["modelID"]
    if provider_id not in connected:
        raise RuntimeError(f"OpenCode provider is not connected: {provider_id}")
    for provider in payload.get("all") or []:
        if str(provider.get("id") or "") != provider_id:
            continue
        for model in (provider.get("models") or {}).values():
            if str((model or {}).get("id") or "") != model_id:
                continue
            modalities = [str(item) for item in (((model or {}).get("modalities") or {}).get("input") or [])]
            if modalities and "image" not in modalities:
                raise RuntimeError(f"Selected run model does not support image input: {provider_id}/{model_id}")
            return
    raise RuntimeError(f"Selected run model not found in OpenCode providers: {provider_id}/{model_id}")


def create_opencode_session(config: OpenCodeConfig, title: str) -> OpenCodeConfig:
    payload = request_json(config, "POST", "/session", {"title": title}, query={"directory": config.directory}, timeout=30) or {}
    session_id = str(payload.get("id") or "").strip()
    if not session_id:
        raise RuntimeError("OpenCode failed to create a recovery session")
    return replace(config, session_id=session_id)


def session_has_stale_assistant(config: OpenCodeConfig, stale_after_seconds: int = 60) -> bool:
    try:
        messages = request_json(config, "GET", f"/session/{config.session_id}/message", None, query={"directory": config.directory, "limit": "8"}, timeout=30) or []
    except Exception:
        return True
    now_ms = int(time.time() * 1000)
    for message in reversed(messages):
        info = message.get("info") or {}
        if info.get("role") != "assistant":
            continue
        completed = int(((info.get("time") or {}).get("completed") or 0) or 0)
        created = int(((info.get("time") or {}).get("created") or 0) or 0)
        if completed:
            return False
        if created and now_ms - created > stale_after_seconds * 1000:
            return True
        return False
    return False


def resolve_paths(workspace: Path | None, output_dir: Path | None, transcripts_dir: Path | None) -> Paths:
    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    meta_dir = output_dir.expanduser().resolve() if output_dir else (resolved_workspace / "meta" if resolved_workspace else Path.cwd() / "meta")
    resolved_transcripts = transcripts_dir.expanduser().resolve() if transcripts_dir else (resolved_workspace / "transcripts" if resolved_workspace else Path.cwd() / "transcripts")
    return Paths(workspace=resolved_workspace, meta_dir=meta_dir, transcripts_dir=resolved_transcripts)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def rel_path(path: Path, workspace: Path | None) -> str:
    if workspace:
        try:
            return path.relative_to(workspace).as_posix()
        except ValueError:
            pass
    return str(path)


def last_completed_assistant(messages: list[dict[str, Any]], started_after: int) -> str | None:
    for message in reversed(messages):
        info = message.get("info") or {}
        if info.get("role") != "assistant":
            continue
        completed = int(((info.get("time") or {}).get("completed") or 0) or 0)
        if completed < started_after:
            continue
        texts = [str(part.get("text") or "") for part in (message.get("parts") or []) if part.get("type") == "text"]
        text = "\n".join([item.strip() for item in texts if item.strip()]).strip()
        if text:
            return text
    return None


def image_file_part(path: Path, workspace: Path | None) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    filename = rel_path(path, workspace)
    return {"type": "file", "mime": mime, "filename": filename, "url": f"data:{mime};base64,{encoded}"}


def compressed_image_path(paths: Paths, source: Path, max_side: int, quality: int) -> Path:
    root = paths.workspace or paths.meta_dir.parent
    out_dir = root / "keyframes" / "segment_descriptor_compressed"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.stem)[:120]
    return out_dir / f"{stem}_max{max_side}_q{quality}.jpg"


def compress_image(paths: Paths, source: Path, max_side: int, quality: int) -> Path:
    target = compressed_image_path(paths, source, max_side, quality)
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    try:
        from PIL import Image  # type: ignore

        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side))
            image.save(target, "JPEG", quality=quality, optimize=True)
        return target
    except Exception:
        pass
    try:
        import cv2  # type: ignore

        image = cv2.imread(str(source))
        if image is None:
            raise RuntimeError(f"cannot read image: {source}")
        height, width = image.shape[:2]
        scale = min(1.0, float(max_side) / max(height, width))
        if scale < 1.0:
            image = cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(target), image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        return target
    except Exception as exc:
        raise RuntimeError("Image compression requires Pillow or OpenCV") from exc


def call_opencode(config: OpenCodeConfig, paths: Paths, prompt: str, image_paths: list[Path], system_prompt: str, timeout_seconds: int) -> str:
    started_at = int(time.time() * 1000)
    parts = [{"type": "text", "text": prompt}] + [image_file_part(path, paths.workspace) for path in image_paths]
    payload = {"parts": parts, "model": config.model, "system": system_prompt}
    request_json(config, "POST", f"/session/{config.session_id}/prompt_async", payload, query={"directory": config.directory}, timeout=30)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        messages = request_json(config, "GET", f"/session/{config.session_id}/message", None, query={"directory": config.directory, "limit": "180"}, timeout=30) or []
        text = last_completed_assistant(messages, started_at)
        if text:
            return text
        time.sleep(2)
    raise RuntimeError("OpenCode timed out before returning segment retake description JSON")


def extract_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", value, re.S)
    if fenced:
        value = fenced.group(1).strip()
    else:
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            value = value[start : end + 1]
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object")
    return parsed


def build_system_prompt() -> str:
    return """
你是短视频复拍描述生成器。你会收到一个时间片段、字幕、语义结构、关键帧图片和字段 schema。
你的任务是根据画面与字幕生成可执行的复拍描述，优先忠实于图片中真实出现的场景、人物、道具、动作和情绪。
不要套用餐饮管理、门店经营等默认行业话术，除非输入画面或文本明确属于该行业。
只返回合法 JSON 对象，不要 Markdown、不要解释文字、不要代码块。
""".strip()


def build_vlm_prompt(final_prompt: str, segment: dict[str, Any], subtitles: list[dict[str, Any]], keyframes: list[dict[str, Any]]) -> str:
    schema = {key: "string" for key in RETAKE_FIELD_KEYS}
    public_keyframes = [{"time": item.get("time"), "role": item.get("role"), "path": item.get("path")} for item in keyframes]
    payload = {
        "task": "为该 detail 片段生成 retake_fields。",
        "rules": [
            "必须返回 JSON 对象，顶层只包含 retake_fields 和 confidence。",
            "retake_fields 必须包含 required_fields 中全部字段，全部字段值为中文字符串。",
            "spoken_script 必须来自 subtitle_items 或 segment.dialogue_text，不要改写台词。",
            "visual_content、scene、props、main_action 必须基于图片证据。",
            "如果图片证据不足，在 retake_notes 中说明需要补拍或人工确认。",
        ],
        "required_fields": RETAKE_FIELD_KEYS,
        "retake_fields_schema": schema,
        "final_prompt_context": final_prompt[:4000],
        "segment": segment,
        "subtitle_items": subtitles,
        "selected_keyframes": public_keyframes,
        "response_example": {"retake_fields": schema, "confidence": 0.85},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def validate_retake_fields(payload: dict[str, Any]) -> dict[str, str]:
    fields = payload.get("retake_fields") if isinstance(payload, dict) else None
    if not isinstance(fields, dict):
        raise ValueError("VLM response must contain retake_fields object")
    normalized: dict[str, str] = {}
    missing = []
    for key in RETAKE_FIELD_KEYS:
        value = str(fields.get(key) or "").strip()
        if key == "spoken_script" and not value:
            value = "无口播或无可用字幕。"
        if not value:
            missing.append(key)
        normalized[key] = value
    if missing:
        raise ValueError(f"VLM response missing retake fields: {', '.join(missing)}")
    return normalized


def normalize_retake_payload(payload: dict[str, Any]) -> dict[str, Any]:
    fields = validate_retake_fields(payload)
    confidence = safe_float(payload.get("confidence"), 0.0)
    return {"retake_fields": fields, "confidence": confidence}


def repair_retake_json(config: OpenCodeConfig, paths: Paths, bad_text: str, error: str, timeout_seconds: int) -> dict[str, Any]:
    prompt = f"""
请把下面内容修复为合法 JSON 对象。顶层必须只包含 retake_fields 和 confidence。
retake_fields 必须包含这些字段：{', '.join(RETAKE_FIELD_KEYS)}
只返回 JSON，不要解释。

错误：{error}

原始内容：
{bad_text}
""".strip()
    text = call_opencode(config, paths, prompt, [], build_system_prompt(), timeout_seconds)
    payload = extract_json_object(text)
    validate_retake_fields(payload)
    return payload


def load_scheme(meta_dir: Path, scheme: str) -> list[dict[str, Any]]:
    path = meta_dir / f"scheme_{scheme}_segments.json"
    if not path.exists():
        raise DependencyError(f"14 requires 13 FineTimelineBuilder output: {path}")
    payload = read_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise DependencyError(f"Invalid 13 output: {path} must contain non-empty items list")
    return [item for item in items if isinstance(item, dict)]


def load_asr_segments(meta_dir: Path) -> list[dict[str, Any]]:
    path = meta_dir / "asr_segments.json"
    if not path.exists():
        raise DependencyError(f"14 requires ASR output for subtitle cutting: {path}")
    payload = read_json(path)
    items = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise DependencyError(f"Invalid ASR output: {path} must contain segments list")
    return [item for item in items if isinstance(item, dict)]


def load_keyframes(meta_dir: Path) -> list[dict[str, Any]]:
    path = meta_dir / "visual_keyframes.json"
    if not path.exists():
        raise DependencyError(f"14 requires 05 visual keyframe output: {path}")
    payload = read_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise DependencyError(f"Invalid visual keyframe output: {path} must contain non-empty items list")
    return [item for item in items if isinstance(item, dict)]


def overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def subtitle_items_for_segment(segment: dict[str, Any], asr_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"))
    rows = []
    for item in asr_segments:
        item_start = safe_float(item.get("start"))
        item_end = safe_float(item.get("end"), item_start)
        if overlap_seconds(start, end, item_start, item_end) <= 0:
            continue
        clipped_start = max(item_start, start)
        clipped_end = min(item_end, end)
        rows.append({
            "index": len(rows) + 1,
            "start": round(clipped_start - start, 3),
            "end": round(clipped_end - start, 3),
            "absolute_start": round(clipped_start, 3),
            "absolute_end": round(clipped_end, 3),
            "text": str(item.get("text") or "").strip(),
            "source_asr_index": item.get("index"),
        })
    semantic_rows = semantic_subtitle_items_for_segment(segment, rows)
    return semantic_rows or rows


def normalize_subtitle_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", re.sub(r"\s+", "", str(text or ""))).lower()


def split_dialogue_text(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*|[；;]\s*", normalized) if part.strip()]
    return parts or [normalized]


def semantic_subtitle_items_for_segment(segment: dict[str, Any], asr_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dialogue_text = str(segment.get("dialogue_text") or "").strip()
    if not dialogue_text:
        return []
    asr_text = "".join(str(item.get("text") or "") for item in asr_rows).strip()
    if normalize_subtitle_text(dialogue_text) == normalize_subtitle_text(asr_text):
        return []
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"))
    duration = max(0.001, end - start)
    parts = split_dialogue_text(dialogue_text)
    if not parts:
        return []
    rows = []
    slice_duration = duration / len(parts)
    for index, part in enumerate(parts, start=1):
        local_start = round((index - 1) * slice_duration, 3)
        local_end = round(duration if index == len(parts) else index * slice_duration, 3)
        rows.append({
            "index": index,
            "start": local_start,
            "end": max(local_end, local_start + 0.001),
            "absolute_start": round(start + local_start, 3),
            "absolute_end": round(start + max(local_end, local_start + 0.001), 3),
            "text": part,
            "source": "semantic_dialogue_text",
        })
    return rows


def srt_timestamp(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_srt(items: list[dict[str, Any]]) -> str:
    blocks = []
    for index, item in enumerate(items, start=1):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        blocks.append(f"{index}\n{srt_timestamp(safe_float(item.get('start')))} --> {srt_timestamp(safe_float(item.get('end')))}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def selected_keyframes(segment: dict[str, Any], keyframes: list[dict[str, Any]], max_count: int) -> list[dict[str, Any]]:
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"))
    midpoint = (start + end) / 2.0
    scoped = []
    for item in keyframes:
        time_value = safe_float(item.get("time"), -1.0)
        if start - 0.25 <= time_value <= end + 0.25 and item.get("path"):
            scoped.append({
                "role": item.get("role") or "evidence",
                "time": round(time_value, 3),
                "path": item.get("path"),
                "source": item.get("source") or "visual_keyframe",
                "distance_to_midpoint": round(abs(time_value - midpoint), 3),
            })
    scoped.sort(key=lambda item: (item["distance_to_midpoint"], abs(item["time"] - start)))
    chosen = scoped[:max_count]
    chosen.sort(key=lambda item: item["time"])
    return chosen


def parse_int_set(value: str | None) -> set[int]:
    result: set[int] = set()
    for part in re.split(r"[,，\s]+", str(value or "").strip()):
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            if start.strip().isdigit() and end.strip().isdigit():
                result.update(range(int(start), int(end) + 1))
            continue
        if part.isdigit():
            result.add(int(part))
    return result


def segment_cache_paths(paths: Paths, idx: int) -> tuple[Path, Path]:
    raw_dir = paths.meta_dir / "segment_descriptions" / "scheme_detail" / "raw"
    return raw_dir / f"segment_{idx:03d}_vlm_request.json", raw_dir / f"segment_{idx:03d}_vlm_response.json"


def progress_paths(paths: Paths) -> tuple[Path, Path]:
    raw_dir = paths.meta_dir / "segment_descriptions" / "scheme_detail" / "raw"
    return raw_dir / "vlm_progress.json", raw_dir / "vlm_progress.jsonl"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit_progress(paths: Paths, event: str, stats: dict[str, Any], segment: dict[str, Any] | None = None, config: OpenCodeConfig | None = None, message: str = "") -> None:
    idx = int((segment or {}).get("index") or stats.get("current") or 0)
    total = int(stats.get("total") or 0)
    completed = int(stats.get("completed") or 0)
    cache_hits = int(stats.get("cache_hits") or 0)
    calls = int(stats.get("calls") or 0)
    failed = int(stats.get("failed") or 0)
    payload = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "mode": "vlm",
        "event": event,
        "timestamp": utc_now_iso(),
        "total": total,
        "current": idx,
        "completed": completed,
        "cache_hits": cache_hits,
        "calls": calls,
        "failed": failed,
        "segment_index": idx,
        "segment_title": (segment or {}).get("title") or "",
        "message": message,
        "opencrew_task_id": config.task_id if config else None,
        "opencode_session_id": config.session_id if config else "",
        "model": config.model if config else {},
    }
    snapshot_path, jsonl_path = progress_paths(paths)
    write_json(snapshot_path, payload)
    append_jsonl(jsonl_path, payload)
    print(
        f"[14 VLM] event={event} total={total} current={idx} completed={completed} "
        f"cache_hits={cache_hits} calls={calls} failed={failed} segment={idx:03d} {message}".rstrip(),
        flush=True,
    )


def load_cached_vlm_response(paths: Paths, idx: int, force_rerun: set[int]) -> dict[str, Any] | None:
    if idx in force_rerun:
        return None
    _, response_path = segment_cache_paths(paths, idx)
    if not response_path.exists():
        return None
    payload = read_json(response_path)
    if isinstance(payload, dict) and payload.get("status") == "completed" and isinstance(payload.get("payload"), dict):
        payload["payload"] = normalize_retake_payload(payload["payload"])
        return payload
    return None


def call_vlm_for_segment(paths: Paths, config: OpenCodeConfig, segment: dict[str, Any], subtitles: list[dict[str, Any]], keyframes: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    idx = int(segment.get("index") or 0)
    request_path, response_path = segment_cache_paths(paths, idx)
    image_paths = []
    for item in keyframes:
        raw_path = Path(str(item.get("path") or ""))
        if not raw_path.is_absolute() and paths.workspace:
            raw_path = paths.workspace / raw_path
        if raw_path.exists():
            image_paths.append(compress_image(paths, raw_path, int(args.image_max_side), int(args.image_quality)))
    if not image_paths:
        raise DependencyError(f"description-mode=vlm requires at least one keyframe image for detail segment {idx:03d}")
    prompt = build_vlm_prompt(config.final_prompt, segment, subtitles, keyframes)
    write_json(request_path, {
        "segment_index": idx,
        "tool_version": TOOL_VERSION,
        "opencrew_task_id": config.task_id,
        "opencode_session_id": config.session_id,
        "model": config.model,
        "prompt": json.loads(prompt),
        "image_paths": [str(path) for path in image_paths],
    })
    try:
        raw_text = call_opencode(config, paths, prompt, image_paths, build_system_prompt(), int(args.timeout_seconds))
        payload = normalize_retake_payload(extract_json_object(raw_text))
    except Exception as exc:
        payload = normalize_retake_payload(repair_retake_json(config, paths, raw_text if "raw_text" in locals() else "", str(exc), int(args.timeout_seconds)))
    result = {
        "segment_index": idx,
        "status": "completed",
        "tool_version": TOOL_VERSION,
        "opencrew_task_id": config.task_id,
        "opencode_session_id": config.session_id,
        "model": config.model,
        "payload": payload,
        "raw_response": raw_text[:8000] if "raw_text" in locals() else "",
    }
    write_json(response_path, result)
    return result


def compact_text(value: str, max_chars: int = 64) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_chars].rstrip()


def neutral_retake_text(value: str) -> str:
    text = str(value or "")
    replacements = {
        "潜在客户": "到店咨询者",
        "目标客户": "目标人群",
        "客户": "咨询者",
        "顾客": "体验者",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def segment_context_text(segment: dict[str, Any], subtitles: list[dict[str, Any]] | None = None, final_prompt: str = "") -> str:
    subtitle_text = "".join(str(item.get("text") or "") for item in (subtitles or [])).strip()
    visual_text = " ".join(str(item or "") for item in (segment.get("visual_text_context") or []))
    return " ".join(
        str(value or "")
        for value in [
            final_prompt,
            segment.get("title"),
            segment.get("dialogue_text"),
            segment.get("semantic_role"),
            segment.get("formula_slot"),
            subtitle_text,
            visual_text,
        ]
    )


def supports_restaurant_domain(text: str) -> bool:
    return any(word in text for word in RESTAURANT_TERMS)


def unsupported_restaurant_template(text: str, evidence_text: str) -> bool:
    return bool(text) and supports_restaurant_domain(text) and not supports_restaurant_domain(evidence_text)


def infer_business_context(final_prompt: str, segment: dict[str, Any], subtitles: list[dict[str, Any]]) -> BusinessContext:
    text = segment_context_text(segment, subtitles, final_prompt)
    if any(word in text for word in HEALTH_MANAGEMENT_TERMS):
        return BusinessContext(
            scene="身体管理/减脂服务沟通场景",
            performer="身体管理顾问、门店工作人员、到店咨询者或体验者，根据原片人物关系安排。",
            character_profile="顾问或工作人员直接回应到店顾虑，咨询者呈现真实犹豫、追问或被说服的状态。",
            props="门店环境、咨询沟通区、手机评论/聊天画面、身体管理相关物料或原片中真实出现的物件。",
            target_audience="有减脂、体重管理、健康管理需求，同时对到店或方案存在顾虑的目标人群。",
            product_focus="身体管理服务、减脂方案信任建立、到店顾虑化解和真实咨询转化。",
            people_coordination="主讲/顾问推动对话，咨询者或现场人员用追问、回应和表情承接顾虑。",
            domain_label="身体管理",
        )
    if supports_restaurant_domain(text):
        return BusinessContext(
            scene="原片明确呈现的现场服务场景",
            performer="原片中的主讲人、现场工作人员或相关配合人员。",
            character_profile="主讲人推进问题或信息表达，现场人物保持真实反应和工作状态。",
            props="原片中真实出现的现场环境、工具、产品或展示物料。",
            target_audience="与原片业务诉求相关的经营者、工作人员或目标人群。",
            product_focus="原片明确表达的服务效率、现场体验、协同关系或业务诉求。",
            people_coordination="主讲人推动对话，相关人物配合回应或展示现场状态。",
            domain_label="现场服务",
        )
    final_prompt_hint = compact_text(final_prompt, 42)
    focus = final_prompt_hint or "原片对应的服务、产品卖点和用户转化目标"
    return BusinessContext(
        scene="原片对应的真实服务沟通/现场互动场景",
        performer="原片中的主讲人、咨询者、体验者或相关配合人员。",
        character_profile="人物状态应贴近原片对白关系，保留真实反应、疑问和推动动作。",
        props="现场环境、手机/展示物料、字幕或画面中真实出现的关键物件。",
        target_audience=f"与“{focus}”相关的目标人群。",
        product_focus=focus,
        people_coordination="主讲人推动信息表达，其他人物用回应、动作或表情承接对话逻辑。",
        domain_label="通用服务",
    )


def visual_context_hint(segment: dict[str, Any], keyframes: list[dict[str, Any]]) -> str:
    ocr_items = [str(item).strip() for item in (segment.get("visual_text_context") or []) if str(item).strip()]
    if ocr_items:
        return f"OCR/画面文字出现“{compact_text('；'.join(ocr_items), 80)}”。"
    if keyframes:
        return "需参考该片段关键帧中的人物位置、环境和动作。"
    return "当前无可用关键帧，需依据对白、语义和业务上下文复拍，并在现场确认画面细节。"


def infer_scene(segment: dict[str, Any], subtitles: list[dict[str, Any]], final_prompt: str) -> str:
    text = segment_context_text(segment, subtitles, final_prompt)
    if any(word in text for word in ["后厨", "厨房", "厨师", "炒菜", "保温柜"]):
        return "原片明确呈现的现场操作区"
    if any(word in text for word in ["前厅", "宴会", "上菜"]):
        return "原片明确呈现的现场服务场景"
    if any(word in text for word in HEALTH_MANAGEMENT_TERMS):
        return "身体管理/减脂服务沟通场景"
    if any(word in text for word in ["导流", "抖音"]):
        return "平台导流收尾画面"
    return "原片真实服务场景"


def structure_label(segment: dict[str, Any]) -> str:
    slot = str(segment.get("formula_slot") or "")
    role = str(segment.get("semantic_role") or "")
    if slot:
        return slot
    if role:
        return role
    return "结构片段"


def rule_retake_fields(segment: dict[str, Any], subtitles: list[dict[str, Any]], keyframes: list[dict[str, Any]], scheme: str, final_prompt: str = "") -> dict[str, str]:
    title = neutral_retake_text(str(segment.get("title") or f"片段{segment.get('index')}"))
    dialogue = "".join(str(item.get("text") or "") for item in subtitles).strip() or str(segment.get("dialogue_text") or "").strip()
    business = infer_business_context(final_prompt, segment, subtitles)
    scene = infer_scene(segment, subtitles, final_prompt)
    structure = structure_label(segment)
    role = neutral_retake_text(str(segment.get("semantic_role") or structure))
    has_visual = bool(keyframes)
    visual_hint = visual_context_hint(segment, keyframes)
    evidence_note = f"{visual_hint}对白核心为“{compact_text(dialogue, 70)}”。" if dialogue else visual_hint
    fields = {
        "guide": f"围绕“{title}”推进{structure}，让观众理解本段在{business.domain_label}内容中的核心信息。",
        "summary": f"本段聚焦{title}，承担{role}作用，重点呈现对白与画面共同表达的顾虑、回应或转化动作。",
        "video_structure": structure,
        "shooting_method": "现场跟拍结合中景对话，必要时补充环境、表情与动作特写。" if has_visual else "根据对白、OCR和业务语义设计现场跟拍与对话镜头。",
        "camera": "手持或稳定器跟拍，优先保证人物关系和现场环境清晰。",
        "shot_type": "中景为主，穿插近景/特写交代关键动作或物件。",
        "visual_content": f"画面应围绕{scene}展开，突出“{title}”相关的人物、环境和关键动作。{evidence_note}",
        "scene": scene,
        "main_scene": scene,
        "people_coordination": business.people_coordination,
        "performer": business.performer,
        "character_profile": business.character_profile,
        "props": business.props,
        "emotion": "真实、紧张、推进解决" if structure in {"问题", "过程"} else "收束、明确、给出方案",
        "emotion_trigger": f"由“{title}”中的冲突、问题或方案推进触发情绪变化。",
        "spoken_script": dialogue,
        "target_audience": business.target_audience,
        "content_highlights": f"突出{title}中的真实顾虑、现场证据、人物反应和解决方向。",
        "product_or_business_focus": business.product_focus,
        "main_action": "围绕对白完成提问、回应、解释顾虑或推动下一步行动。",
        "camera_movement": "跟随人物移动，关键句可短暂停留，转场处用自然切镜。",
        "composition": "人物居中或三分构图，保留足够环境信息证明场景真实。",
        "transition_type": "自然切换/动作转场，避免生硬跳切。",
        "editing_notes": "保留关键口播句和现场反应，必要时插入环境 B-roll 强化可信度。",
        "retake_notes": "复拍时不要只拍口播，要同时拍到人物关系、现场环境和能支撑观点的关键画面。",
        "visual_must_have": f"至少拍到{scene}、主要出镜人、与本段主题相关的动作、表情或画面文字。",
    }
    if scheme == "detail":
        fields["summary"] = f"最细片段：{fields['summary']}"
    elif scheme == "summary":
        fields["summary"] = f"公式槽位汇总：{fields['summary']}"
    return fields


def aggregate_text(values: list[str], fallback: str = "") -> str:
    cleaned = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return "；".join(cleaned[:4]) if cleaned else fallback


def aggregate_retake_fields(segment: dict[str, Any], source_descriptions: list[dict[str, Any]], subtitles: list[dict[str, Any]], keyframes: list[dict[str, Any]], scheme: str, final_prompt: str = "") -> dict[str, str]:
    if not source_descriptions:
        return rule_retake_fields(segment, subtitles, keyframes, scheme, final_prompt)
    source_fields = [item.get("retake_fields") or {} for item in source_descriptions]
    fields: dict[str, str] = {}
    fallback_fields = rule_retake_fields(segment, subtitles, keyframes, scheme, final_prompt)
    evidence_text = segment_context_text(segment, subtitles, final_prompt)
    for key in RETAKE_FIELD_KEYS:
        if key == "spoken_script":
            fields[key] = "".join(str(item.get("text") or "") for item in subtitles).strip() or aggregate_text([f.get(key, "") for f in source_fields])
        elif key in {"summary", "guide", "retake_notes", "editing_notes"}:
            fields[key] = aggregate_text([f.get(key, "") for f in source_fields], f"聚合{scheme}片段：{segment.get('title') or ''}")
        else:
            fields[key] = aggregate_text([f.get(key, "") for f in source_fields])
        if not fields[key] or unsupported_restaurant_template(fields[key], evidence_text):
            fields[key] = fallback_fields[key]
    fields["video_structure"] = structure_label(segment)
    return fields


def detail_sources_for_segment(segment: dict[str, Any], detail_descriptions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"))
    rows = []
    for item in detail_descriptions:
        time = item.get("time") or {}
        if overlap_seconds(start, end, safe_float(time.get("start")), safe_float(time.get("end"))) > 0:
            rows.append(item)
    return rows


def build_description(scheme: str, segment: dict[str, Any], subtitles: list[dict[str, Any]], keyframes: list[dict[str, Any]], source_detail: list[dict[str, Any]], subtitle_path: str, description_mode: str, final_prompt: str = "", vlm_result: dict[str, Any] | None = None) -> dict[str, Any]:
    if scheme == "detail":
        if description_mode == "vlm":
            if not vlm_result:
                raise RuntimeError("Internal error: detail VLM mode requires vlm_result")
            payload = vlm_result.get("payload") or {}
            retake_fields = validate_retake_fields(payload)
            aggregation = {"method": "direct_detail_vlm_analysis", "source_detail_segment_indices": [], "vlm_called": True}
        else:
            retake_fields = rule_retake_fields(segment, subtitles, keyframes, scheme, final_prompt)
            aggregation = {"method": "direct_detail_rule_analysis", "source_detail_segment_indices": [], "vlm_called": False}
    else:
        retake_fields = aggregate_retake_fields(segment, source_detail, subtitles, keyframes, scheme, final_prompt)
        aggregation = {"method": "from_detail_segment_descriptions", "source_detail_segment_indices": [int(item.get("segment_index") or 0) for item in source_detail], "vlm_called": False}
    warnings = []
    confidence = safe_float((vlm_result or {}).get("payload", {}).get("confidence"), 0.72 if keyframes else 0.55)
    model = (vlm_result or {}).get("model") or {}
    opencode_session_id = (vlm_result or {}).get("opencode_session_id") or ""
    return {
        "schema_version": "1.0",
        "scheme": scheme,
        "segment_index": int(segment.get("index") or 0),
        "segment_id": f"{scheme}_segment_{int(segment.get('index') or 0):03d}",
        "time": {"start": safe_float(segment.get("start")), "end": safe_float(segment.get("end")), "duration": safe_float(segment.get("duration"))},
        "source": {
            "title": segment.get("title") or "",
            "semantic_role": segment.get("semantic_role") or "",
            "formula_slot": segment.get("formula_slot") or "",
            "source_segment_indices": segment.get("source_segment_indices") or [],
            "boundary_source": segment.get("boundary_source") or "",
            "boundary_ref": segment.get("boundary_ref"),
            "confidence": safe_float(segment.get("confidence"), 0.0),
        },
        "subtitle": {"available": bool(subtitles), "subtitle_path": subtitle_path, "dialogue_text": "".join(str(item.get("text") or "") for item in subtitles).strip(), "subtitle_items": subtitles},
        "visual_evidence": {"available": bool(keyframes), "keyframes": keyframes, "evidence_refs": segment.get("evidence_refs") or []},
        "retake_fields": retake_fields,
        "aggregation": aggregation,
        "vlm_input": {"segment": segment, "selected_keyframes": keyframes, "subtitle_items": subtitles, "required_fields": RETAKE_FIELD_KEYS},
        "vlm_analysis": {"used": bool(vlm_result), "mode": description_mode, "model": model, "opencode_session_id": opencode_session_id, "confidence": confidence, "warnings": warnings},
        "quality": {"field_completeness": field_completeness(retake_fields), "subtitle_available": bool(subtitles), "visual_evidence_count": len(keyframes), "needs_human_review": not bool(keyframes) or bool(warnings), "warnings": warnings},
    }


def field_completeness(fields: dict[str, str]) -> float:
    present = sum(1 for key in RETAKE_FIELD_KEYS if str(fields.get(key) or "").strip())
    return round(present / len(RETAKE_FIELD_KEYS), 3)


def process_scheme(paths: Paths, scheme: str, segments: list[dict[str, Any]], asr_segments: list[dict[str, Any]], keyframes: list[dict[str, Any]], detail_descriptions: list[dict[str, Any]], args: argparse.Namespace, config: OpenCodeConfig | None = None, final_prompt: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    scheme_dir = paths.meta_dir / "segment_descriptions" / f"scheme_{scheme}"
    subtitles_dir = paths.transcripts_dir / f"scheme_{scheme}_subtitles"
    descriptions = []
    index_items = []
    vlm_stats = {"calls": 0, "cache_hits": 0, "completed": 0, "failed": 0, "total": len(segments), "current": 0}
    max_keyframes = int(getattr(args, f"max_keyframes_{scheme}")) if scheme in SCHEMES else 3
    force_rerun = parse_int_set(getattr(args, "force_rerun_segments", ""))
    for segment in segments:
        idx = int(segment.get("index") or len(descriptions) + 1)
        subtitles = subtitle_items_for_segment(segment, asr_segments)
        srt_rel = f"transcripts/scheme_{scheme}_subtitles/segment_{idx:03d}.srt"
        srt_abs = subtitles_dir / f"segment_{idx:03d}.srt"
        write_text(srt_abs, render_srt(subtitles))
        if scheme == "detail":
            selected = selected_keyframes(segment, keyframes, max_keyframes)
            source_detail = []
            vlm_result = None
            if str(args.description_mode) == "vlm":
                if config is None:
                    raise RuntimeError("description-mode=vlm requires OpenCode config")
                vlm_stats["current"] = idx
                emit_progress(paths, "segment_start", vlm_stats, segment, config)
                cached = load_cached_vlm_response(paths, idx, force_rerun) if bool(args.resume) else None
                if cached is not None:
                    vlm_result = cached
                    vlm_stats["cache_hits"] += 1
                    vlm_stats["completed"] += 1
                    emit_progress(paths, "cache_hit", vlm_stats, segment, config)
                else:
                    emit_progress(paths, "calling", vlm_stats, segment, config)
                    try:
                        vlm_result = call_vlm_for_segment(paths, config, segment, subtitles, selected, args)
                        vlm_stats["calls"] += 1
                        vlm_stats["completed"] += 1
                        emit_progress(paths, "completed", vlm_stats, segment, config)
                    except Exception as exc:
                        vlm_stats["failed"] += 1
                        emit_progress(paths, "failed", vlm_stats, segment, config, str(exc))
                        raise
        else:
            source_detail = detail_sources_for_segment(segment, detail_descriptions)
            vlm_result = None
            selected = []
            for item in source_detail:
                for frame in (item.get("visual_evidence") or {}).get("keyframes") or []:
                    selected.append({**frame, "source_detail_segment_index": item.get("segment_index")})
            selected = selected[:max_keyframes]
        desc = build_description(scheme, segment, subtitles, selected, source_detail, srt_rel, str(args.description_mode), final_prompt, vlm_result)
        desc_path = scheme_dir / f"segment_{idx:03d}.json"
        write_json(desc_path, desc)
        descriptions.append(desc)
        index_items.append({"segment_index": idx, "description_path": str(desc_path), "subtitle_path": str(srt_abs), "time": desc["time"], "title": desc["source"]["title"]})
    return descriptions, index_items, vlm_stats


def run_builder(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    selected_schemes = [item.strip() for item in str(args.schemes).split(",") if item.strip()] or list(SCHEMES)
    invalid = [scheme for scheme in selected_schemes if scheme not in SCHEMES]
    if invalid:
        raise DependencyError(f"Invalid schemes requested: {', '.join(invalid)}")
    schemes = {scheme: load_scheme(paths.meta_dir, scheme) for scheme in selected_schemes}
    asr_segments = load_asr_segments(paths.meta_dir)
    keyframes = load_keyframes(paths.meta_dir)
    config = None
    final_prompt = ""
    database_url = os.environ.get(str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL
    if str(args.description_mode) == "vlm":
        if args.task_id is None:
            raise DependencyError("description-mode=vlm requires --task-id, e.g. --task-id 8")
        config = fetch_opencode_config(database_url, int(args.task_id))
        final_prompt = config.final_prompt
        ensure_model_supports_image(config)
        if bool(args.fresh_session) or session_has_stale_assistant(config):
            config = create_opencode_session(config, f"{TOOL_NAME} Task #{int(args.task_id)}")
    elif args.task_id is not None:
        final_prompt = fetch_task_final_prompt(database_url, int(args.task_id))
    indexes: dict[str, list[dict[str, Any]]] = {}
    descriptions: dict[str, list[dict[str, Any]]] = {}
    detail_descriptions: list[dict[str, Any]] = []
    detail_vlm_stats = {"calls": 0, "cache_hits": 0, "completed": 0, "failed": 0}
    if "detail" in selected_schemes:
        detail_descriptions, detail_index, detail_vlm_stats = process_scheme(paths, "detail", schemes["detail"], asr_segments, keyframes, [], args, config, final_prompt)
        indexes["detail"] = detail_index
        descriptions["detail"] = detail_descriptions
    if "balanced" in selected_schemes:
        balanced_descriptions, balanced_index, _ = process_scheme(paths, "balanced", schemes["balanced"], asr_segments, keyframes, detail_descriptions, args, config, final_prompt)
        indexes["balanced"] = balanced_index
        descriptions["balanced"] = balanced_descriptions
    if "summary" in selected_schemes:
        summary_descriptions, summary_index, _ = process_scheme(paths, "summary", schemes["summary"], asr_segments, keyframes, detail_descriptions, args, config, final_prompt)
        indexes["summary"] = summary_index
        descriptions["summary"] = summary_descriptions
    for scheme, items in indexes.items():
        write_json(paths.meta_dir / f"scheme_{scheme}_segment_descriptions.json", {"scheme": scheme, "items": items})
        write_json(paths.meta_dir / f"dialogue_segments_scheme_{scheme}.json", {"scheme": scheme, "items": [{"segment_index": item["segment_index"], "subtitle_path": item["subtitle_path"], "description_path": item["description_path"]} for item in items]})
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace": str(paths.workspace) if paths.workspace else "",
        "description_mode": str(args.description_mode),
        "selected_schemes": selected_schemes,
        "outputs": {f"{scheme}_index": str(paths.meta_dir / f"scheme_{scheme}_segment_descriptions.json") for scheme in indexes},
        "counts": {scheme: len(items) for scheme, items in indexes.items()},
        "vlm": {
            "enabled": str(args.description_mode) == "vlm",
            "opencrew_task_id": int(args.task_id) if args.task_id is not None else None,
            "opencrew_session_id": config.opencrew_session_id if config else None,
            "opencode_session_id": config.session_id if config else None,
            "model": config.model if config else {},
            "detail_segment_calls": detail_vlm_stats["calls"],
            "detail_segment_cache_hits": detail_vlm_stats["cache_hits"],
            "detail_segments_completed": detail_vlm_stats["completed"],
            "detail_segments_failed": detail_vlm_stats["failed"],
            "progress_snapshot": str(progress_paths(paths)[0]) if str(args.description_mode) == "vlm" else "",
            "progress_log": str(progress_paths(paths)[1]) if str(args.description_mode) == "vlm" else "",
        },
        "quality": {scheme: {"min_field_completeness": min([item["quality"]["field_completeness"] for item in descs] or [0.0]), "items_needing_review": sum(1 for item in descs if item["quality"]["needs_human_review"])} for scheme, descs in descriptions.items()},
    }
    write_json(paths.meta_dir / "14_segment_descriptor_subtitle_builder_result.json", result)
    return result


def failed_result(paths: Paths, message: str) -> dict[str, Any]:
    lower = message.lower()
    if "does not support image" in lower:
        error_code = "model_does_not_support_image"
    elif "opencode" in lower or "run model" in lower or "task #" in lower:
        error_code = "opencode_dependency_error"
    else:
        error_code = "missing_dependency"
    result = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "failed", "workspace": str(paths.workspace) if paths.workspace else "", "error_code": error_code, "message": message, "required_dependencies": ["13 scheme outputs", "02 asr_segments.json", "05 visual_keyframes.json", "Task OpenCode session and image-capable Run Model when --description-mode=vlm"]}
    write_json(paths.meta_dir / "14_segment_descriptor_subtitle_builder_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build per-segment JSON retake descriptions and subtitles for all timeline schemes.")
    parser.add_argument("--workspace", help="Task workspace path. Defaults outputs to <workspace>/meta and <workspace>/transcripts.")
    parser.add_argument("--output-dir", help="Explicit meta output directory. Overrides --workspace/meta.")
    parser.add_argument("--transcripts-dir", help="Explicit transcripts output directory. Overrides --workspace/transcripts.")
    parser.add_argument("--description-mode", choices=["rule", "vlm"], default="rule")
    parser.add_argument("--schemes", default="detail,balanced,summary", help="Comma-separated schemes to process. Choices: detail,balanced,summary.")
    parser.add_argument("--task-id", type=int, help="OpenClip task id used for final_prompt, OpenCode session, and run model in VLM mode.")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--resume", action="store_true", help="Reuse completed detail VLM raw response cache files.")
    parser.add_argument("--force-rerun-segments", default="", help="Comma/range list of detail segment indexes to rerun, e.g. 1,2,3 or 1-31.")
    parser.add_argument("--fresh-session", action="store_true", help="Create a fresh OpenCode session for this run while keeping the Task final_prompt and run model.")
    parser.add_argument("--image-max-side", type=int, default=1024)
    parser.add_argument("--image-quality", type=int, default=75)
    parser.add_argument("--max-keyframes-detail", type=int, default=3)
    parser.add_argument("--max-keyframes-balanced", type=int, default=8)
    parser.add_argument("--max-keyframes-summary", type=int, default=12)
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    paths = resolve_paths(Path(args.workspace) if args.workspace else None, Path(args.output_dir) if args.output_dir else None, Path(args.transcripts_dir) if args.transcripts_dir else None)
    try:
        result = run_builder(paths, args)
        exit_code = 0
    except (DependencyError, RuntimeError, ValueError) as exc:
        result = failed_result(paths, str(exc))
        exit_code = 2
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("status") == "completed":
        print(f"{TOOL_NAME} completed: {result['outputs']['balanced_index']}")
    else:
        print(f"{TOOL_NAME} failed: {result.get('message')}")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
