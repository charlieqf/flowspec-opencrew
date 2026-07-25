from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOOL_NAME = "DetailSchemeRecomposer"
TOOL_VERSION = "0.1.0"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"

RETAKE_FIELDS = [
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


@dataclass(frozen=True)
class Paths:
    workspace: Path
    meta_dir: Path
    transcripts_dir: Path
    storyboards_dir: Path
    schemes_dir: Path
    reports_dir: Path


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


class DependencyError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
        provider = decode_text(data.get("run_model_provider")).strip()
        model_id = decode_text(data.get("run_model_id")).strip()
        session_id = decode_text(data.get("opencode_session_id")).strip()
        if not provider or not model_id or not session_id:
            raise RuntimeError(f"Task #{task_id} is missing run model or OpenCode session")
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


def create_opencode_session(config: OpenCodeConfig, title: str) -> OpenCodeConfig:
    payload = request_json(config, "POST", "/session", {"title": title}, query={"directory": config.directory}, timeout=30) or {}
    session_id = str(payload.get("id") or "").strip()
    if not session_id:
        raise RuntimeError("OpenCode failed to create recomposer session")
    return replace(config, session_id=session_id)


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


def call_opencode(config: OpenCodeConfig, prompt: str, system_prompt: str, timeout_seconds: int) -> str:
    started_at = int(time.time() * 1000)
    payload = {"parts": [{"type": "text", "text": prompt}], "model": config.model, "system": system_prompt}
    request_json(config, "POST", f"/session/{config.session_id}/prompt_async", payload, query={"directory": config.directory}, timeout=30)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        messages = request_json(config, "GET", f"/session/{config.session_id}/message", None, query={"directory": config.directory, "limit": "120"}, timeout=30) or []
        text = last_completed_assistant(messages, started_at)
        if text:
            return text
        time.sleep(2)
    raise RuntimeError("OpenCode timed out before returning recomposition JSON")


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


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def resolve_paths(workspace: Path) -> Paths:
    resolved = workspace.expanduser().resolve()
    return Paths(workspace=resolved, meta_dir=resolved / "meta", transcripts_dir=resolved / "transcripts", storyboards_dir=resolved / "storyboards", schemes_dir=resolved / "schemes", reports_dir=resolved / "reports")


def load_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise DependencyError(f"missing dependency: {path}")
    payload = read_json(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise DependencyError(f"invalid dependency: {path} must contain non-empty items")
    return [item for item in items if isinstance(item, dict)]


def compact_detail_context(paths: Paths) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    detail_segments = load_items(paths.meta_dir / "scheme_detail_segments.json")
    descriptions: dict[int, dict[str, Any]] = {}
    for segment in detail_segments:
        index = int(segment.get("index") or 0)
        desc_path = paths.meta_dir / "segment_descriptions" / "scheme_detail" / f"segment_{index:03d}.json"
        if desc_path.exists():
            descriptions[index] = read_json(desc_path)
    return detail_segments, descriptions


def build_prompt(target: str, instruction: str, final_prompt: str, detail_segments: list[dict[str, Any]], descriptions: dict[int, dict[str, Any]]) -> str:
    compact = []
    for segment in detail_segments:
        index = int(segment.get("index") or 0)
        retake = (descriptions.get(index) or {}).get("retake_fields") or {}
        compact.append(
            {
                "index": index,
                "start": segment.get("start"),
                "end": segment.get("end"),
                "duration": segment.get("duration"),
                "title": segment.get("title"),
                "formula_slot": segment.get("formula_slot"),
                "semantic_role": segment.get("semantic_role"),
                "dialogue_text": segment.get("dialogue_text"),
                "main_scene": retake.get("main_scene") or retake.get("scene"),
                "people_coordination": retake.get("people_coordination"),
                "main_action": retake.get("main_action"),
                "visual_content": retake.get("visual_content"),
                "spoken_script": retake.get("spoken_script"),
                "summary": retake.get("summary"),
            }
        )
    schema = {
        "target_scheme": target,
        "groups": [
            {
                "index": 1,
                "title": "新分镜标题",
                "logic": "此组承担的业务逻辑",
                "formula_slot": "问题|过程|方案|自定义",
                "detail_indices": [1, 2],
                "retake_fields": {field: "合并后的字段文本" for field in RETAKE_FIELDS},
            }
        ],
    }
    return f"""
请把 detail 分镜重组为新的 {target} 分镜。只允许合并连续的 detail 分镜，不能打乱顺序，不能遗漏，也不能重复。

用户重组提示词：
{instruction}

Task Final Prompt：
{final_prompt}

detail 分镜资料 JSON：
{json.dumps(compact, ensure_ascii=False, indent=2)}

输出要求：
1. 只返回合法 JSON，不要 Markdown，不要解释。
2. groups 必须覆盖所有 detail index，从 1 到 {len(detail_segments)}，且每组 detail_indices 必须连续。
3. 每个组必须根据用户提示词命名并说明 logic。
4. retake_fields 必须填满以下字段：{', '.join(RETAKE_FIELDS)}。
5. retake_fields 必须从 detail 信息合并，不得臆造不存在的人物、场景、动作或结论。

目标 JSON schema：
{json.dumps(schema, ensure_ascii=False, indent=2)}
""".strip()


def build_system_prompt() -> str:
    return """
你是短视频分镜重组工具。你的任务不是重新分析视频，而是根据已有 detail 分镜和用户给出的重组逻辑，把连续 detail 段合并为新的 balanced 或 summary 分镜。
必须遵守：只合并连续 detail 段；不打乱顺序；不遗漏；不重复；不创造不存在的画面、人物、场景、动作或业务结论；只返回 JSON。
""".strip()


def validate_groups(payload: dict[str, Any], detail_count: int) -> list[dict[str, Any]]:
    groups = payload.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("LLM response missing groups")
    expected = 1
    rows: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("group must be an object")
        indices = group.get("detail_indices")
        if not isinstance(indices, list) or not indices:
            raise ValueError("group missing detail_indices")
        clean = [int(item) for item in indices]
        if clean != list(range(clean[0], clean[-1] + 1)):
            raise ValueError(f"detail_indices must be continuous: {clean}")
        if clean[0] != expected:
            raise ValueError(f"detail index coverage gap: expected {expected}, got {clean[0]}")
        expected = clean[-1] + 1
        group = dict(group)
        group["detail_indices"] = clean
        rows.append(group)
    if expected != detail_count + 1:
        raise ValueError(f"detail index coverage incomplete: expected through {detail_count}, got through {expected - 1}")
    return rows


def srt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def load_asr_items(meta_dir: Path) -> list[dict[str, Any]]:
    path = meta_dir / "asr_segments.json"
    if not path.exists():
        return []
    payload = read_json(path)
    segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(segments, list):
        return []
    rows = []
    seen: set[tuple[float, float, str]] = set()
    for item in segments:
        if not isinstance(item, dict):
            continue
        start = safe_float(item.get("start"))
        end = safe_float(item.get("end"))
        text = str(item.get("text") or "").strip()
        key = (round(start, 3), round(end, 3), text)
        if not text or end <= start or key in seen:
            continue
        seen.add(key)
        rows.append({"start": start, "end": end, "text": text, "source_asr_index": item.get("index")})
    return sorted(rows, key=lambda item: safe_float(item.get("start")))


def subtitle_items_for_time(asr_items: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in asr_items:
        abs_start = safe_float(item.get("start"))
        abs_end = safe_float(item.get("end"))
        if abs_start >= end or abs_end <= start:
            continue
        clipped_start = max(abs_start, start)
        clipped_end = min(abs_end, end)
        if clipped_end <= clipped_start:
            continue
        items.append(
            {
                "index": len(items) + 1,
                "start": round(clipped_start - start, 3),
                "end": round(clipped_end - start, 3),
                "absolute_start": round(clipped_start, 3),
                "absolute_end": round(clipped_end, 3),
                "text": str(item.get("text") or ""),
                "source_asr_index": item.get("source_asr_index"),
            }
        )
    return items


def render_srt(items: list[dict[str, Any]]) -> str:
    blocks = []
    for idx, item in enumerate(items, start=1):
        start = safe_float(item.get("absolute_start"), safe_float(item.get("start")))
        end = safe_float(item.get("absolute_end"), safe_float(item.get("end")))
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        blocks.append(f"{idx}\n{srt_time(start)} --> {srt_time(end)}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def remove_old_target_files(paths: Paths, target: str) -> None:
    desc_dir = paths.meta_dir / "segment_descriptions" / f"scheme_{target}"
    sub_dir = paths.transcripts_dir / f"scheme_{target}_subtitles"
    for root, pattern in ((desc_dir, "segment_*.json"), (sub_dir, "segment_*.srt")):
        if root.exists():
            for path in root.glob(pattern):
                path.unlink()


def render_storyboard(target: str, rows: list[dict[str, Any]]) -> str:
    lines = [f"# {target} recomposed storyboard", ""]
    for row in rows:
        lines.append(f"## {row['index']:03d} {row['start']:.3f}-{row['end']:.3f}s")
        lines.append(f"- Title: {row.get('title') or ''}")
        lines.append(f"- Logic: {row.get('logic') or ''}")
        lines.append(f"- Detail indices: {row.get('source_detail_segment_indices') or []}")
        lines.append(f"- Scene: {row.get('main_scene') or ''}")
        lines.append("")
    return "\n".join(lines)


def recompute_coverage(meta_dir: Path, target: str, rows: list[dict[str, Any]], duration: float) -> dict[str, Any]:
    issues = []
    if rows:
        if abs(safe_float(rows[0].get("start")) - 0.0) > 0.01:
            issues.append("first segment does not start at 0")
        if abs(safe_float(rows[-1].get("end")) - duration) > 0.01:
            issues.append("last segment does not end at video duration")
        for left, right in zip(rows, rows[1:]):
            if abs(safe_float(left.get("end")) - safe_float(right.get("start"))) > 0.01:
                issues.append(f"gap_or_overlap between {left.get('index')} and {right.get('index')}")
    else:
        issues.append("no segments")
    return {"scheme": target, "status": "passed" if not issues else "failed", "valid": not issues, "issues": issues, "segment_count": len(rows), "start": rows[0]["start"] if rows else 0.0, "end": rows[-1]["end"] if rows else 0.0, "duration_seconds": duration}


def update_timeline_coverage(paths: Paths, target: str, coverage: dict[str, Any]) -> None:
    path = paths.meta_dir / "timeline_coverage_check.json"
    payload = read_json(path) if path.exists() else {"schemes": {}}
    if not isinstance(payload, dict):
        payload = {"schemes": {}}
    schemes = payload.setdefault("schemes", {})
    schemes[target] = coverage
    write_json(path, payload)


def target_output_name(target: str) -> str:
    return {"balanced": "scheme_2", "summary": "scheme_3"}[target]


def load_video_path(meta_dir: Path, explicit_video: str | None) -> Path:
    if explicit_video:
        path = Path(explicit_video).expanduser().resolve()
        if path.exists():
            return path
        raise FileNotFoundError(f"video file does not exist: {path}")
    for filename, key in (("video_metadata.json", "path"), ("pyscenedetect_cuts.json", "video_path"), ("pyscenedetect_scenes.json", "video_path")):
        path = meta_dir / filename
        if not path.exists():
            continue
        value = read_json(path).get(key)
        if value:
            video = Path(str(value)).expanduser().resolve()
            if video.exists():
                return video
    raise DependencyError("17 export requires source video path from --video or metadata")


def find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg  # type: ignore

            return str(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception as exc:
            raise DependencyError("17 export requires ffmpeg in PATH or imageio_ffmpeg fallback") from exc
    return ffmpeg


def media_env() -> dict[str, str]:
    env = os.environ.copy()
    ffmpeg_dir = str(Path(find_ffmpeg()).parent)
    env["PATH"] = os.pathsep.join([ffmpeg_dir, "/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "")])
    return env


def export_clip(ffmpeg: str, video_path: Path, output_path: Path, start: float, duration: float, mode: str, overwrite: bool) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        return {"status": "skipped_existing", "path": str(output_path), "command": []}
    if mode == "copy":
        command = [ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{duration:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero", str(output_path)]
    else:
        command = [ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-movflags", "+faststart", str(output_path)]
    result = subprocess.run(command, text=True, capture_output=True, env=media_env())
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "ffmpeg clip export failed").strip()
        raise RuntimeError(f"failed to export clip {output_path}: {message}")
    return {"status": "exported", "path": str(output_path), "command": command}


def clear_target_scheme_dir(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("segment_*.mp4", "segment_*.srt", "segment_*.json", "manifest.json"):
        for path in out_dir.glob(pattern):
            path.unlink()


def export_target_scheme(paths: Paths, target: str, rows: list[dict[str, Any]], video_path: Path, clip_mode: str, overwrite: bool) -> dict[str, Any]:
    output_name = target_output_name(target)
    out_dir = paths.schemes_dir / output_name
    if overwrite:
        clear_target_scheme_dir(out_dir)
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
    virtual_source = paths.workspace / "source_video.mp4"
    source_video_path = virtual_source if virtual_source.exists() else video_path
    ffmpeg = None if clip_mode == "virtual" else find_ffmpeg()
    manifest_items = []
    for row in rows:
        idx = int(row.get("index") or len(manifest_items) + 1)
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"))
        duration = max(0.001, end - start)
        source_srt = paths.transcripts_dir / f"scheme_{target}_subtitles" / f"segment_{idx:03d}.srt"
        source_json = paths.meta_dir / "segment_descriptions" / f"scheme_{target}" / f"segment_{idx:03d}.json"
        if not source_srt.exists() or not source_json.exists():
            raise DependencyError(f"target scheme export missing srt/json for segment {idx:03d}")
        srt_path = out_dir / f"segment_{idx:03d}.srt"
        json_path = out_dir / f"segment_{idx:03d}.json"
        mp4_path = out_dir / f"segment_{idx:03d}.mp4"
        shutil.copyfile(source_srt, srt_path)
        shutil.copyfile(source_json, json_path)
        if clip_mode == "virtual":
            clip_result = {"status": "virtual", "path": str(source_video_path), "command": []}
        else:
            clip_result = export_clip(str(ffmpeg), video_path, mp4_path, start, duration, clip_mode, overwrite)
        manifest_items.append({
            "segment_index": idx,
            "source_scheme": target,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
            "title": row.get("title") or "",
            "srt_path": str(srt_path),
            "clip_path": str(source_video_path if clip_mode == "virtual" else mp4_path),
            "source_video_path": str(source_video_path),
            "retake_description_path": str(json_path),
            "clip_status": clip_result["status"],
        })
        print(f"[17 export] scheme={output_name} segment={idx:03d}/{len(rows)} clip={clip_result['status']}", flush=True)
    manifest = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "scheme": target, "output_name": output_name, "video_path": str(video_path), "source_video_path": str(source_video_path), "clip_mode": clip_mode, "items": manifest_items}
    write_json(out_dir / "manifest.json", manifest)
    write_json(paths.reports_dir / f"17_{target}_export_manifest.json", manifest)
    return {"output_name": output_name, "scheme_dir": str(out_dir), "manifest": str(out_dir / "manifest.json"), "segments": len(manifest_items), "clips": len(manifest_items), "srts": len(manifest_items), "retake_descriptions": len(manifest_items)}


def run(args: argparse.Namespace) -> dict[str, Any]:
    paths = resolve_paths(Path(args.workspace))
    target = str(args.target)
    detail_segments, descriptions = compact_detail_context(paths)
    asr_items = load_asr_items(paths.meta_dir)
    duration = safe_float(read_json(paths.meta_dir / "video_metadata.json").get("duration_seconds"), safe_float(detail_segments[-1].get("end")))
    database_url = os.environ.get(str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL
    config = fetch_opencode_config(database_url, int(args.task_id))
    if bool(args.fresh_session):
        config = create_opencode_session(config, f"{TOOL_NAME} Task #{args.task_id}")
    prompt = build_prompt(target, str(args.instruction), config.final_prompt, detail_segments, descriptions)
    raw_text = call_opencode(config, prompt, build_system_prompt(), int(args.timeout_seconds))
    payload = extract_json_object(raw_text)
    groups = validate_groups(payload, len(detail_segments))

    remove_old_target_files(paths, target)
    scheme_rows: list[dict[str, Any]] = []
    description_index: list[dict[str, Any]] = []
    desc_dir = paths.meta_dir / "segment_descriptions" / f"scheme_{target}"
    sub_dir = paths.transcripts_dir / f"scheme_{target}_subtitles"

    detail_by_index = {int(item.get("index") or 0): item for item in detail_segments}
    for new_index, group in enumerate(groups, start=1):
        indices = [int(item) for item in group["detail_indices"]]
        source = [detail_by_index[item] for item in indices]
        start = safe_float(source[0].get("start"))
        end = safe_float(source[-1].get("end"))
        if new_index == 1:
            start = 0.0
        if new_index == len(groups):
            end = round(duration, 3)
        retake = group.get("retake_fields") if isinstance(group.get("retake_fields"), dict) else {}
        subtitle_items = subtitle_items_for_time(asr_items, start, end)
        dialogue_text = "".join([str(item.get("text") or "") for item in subtitle_items]) or "".join([str(item.get("dialogue_text") or "") for item in source])
        title = str(group.get("title") or f"{target} segment {new_index}").strip()
        formula_slot = str(group.get("formula_slot") or source[0].get("formula_slot") or "").strip()
        scheme_row = {
            "scheme": target,
            "index": new_index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "title": title,
            "semantic_role": str(group.get("logic") or "detail_recomposed"),
            "formula_slot": formula_slot,
            "dialogue_text": dialogue_text,
            "confidence": round(sum([safe_float(item.get("confidence"), 0.8) for item in source]) / len(source), 3),
            "source_segment_indices": sorted({idx for item in source for idx in (item.get("source_segment_indices") or []) if isinstance(idx, int)}),
            "source_detail_segment_indices": indices,
            "boundary_source": "17_detail_recomposer",
            "boundary_ref": f"detail_{indices[0]:03d}_{indices[-1]:03d}",
            "boundary_type": "user_prompt_detail_grouping",
            "evidence_refs": [],
        }
        scheme_rows.append(scheme_row)

        srt_path = sub_dir / f"segment_{new_index:03d}.srt"
        write_text(srt_path, render_srt(subtitle_items))
        desc_path = desc_dir / f"segment_{new_index:03d}.json"
        description = {
            "schema_version": "1.0",
            "scheme": target,
            "segment_index": new_index,
            "segment_id": f"{target}_segment_{new_index:03d}",
            "time": {"start": scheme_row["start"], "end": scheme_row["end"], "duration": scheme_row["duration"]},
            "source": {k: scheme_row[k] for k in ["title", "semantic_role", "formula_slot", "source_segment_indices", "boundary_source", "boundary_ref", "confidence"]},
            "subtitle": {"available": bool(subtitle_items), "subtitle_path": srt_path.relative_to(paths.workspace).as_posix(), "dialogue_text": dialogue_text, "subtitle_items": subtitle_items},
            "visual_evidence": {"available": True, "source_detail_segment_indices": indices},
            "retake_fields": {field: str(retake.get(field) or "").strip() for field in RETAKE_FIELDS},
            "aggregation": {"method": "17_detail_scheme_recomposer", "source_detail_segment_indices": indices, "vlm_called": True, "instruction": str(args.instruction)},
            "vlm_analysis": {"used": True, "mode": "text_recompose", "model": config.model, "opencode_session_id": config.session_id, "confidence": 0.9, "warnings": []},
            "quality": {"field_completeness": round(sum(1 for field in RETAKE_FIELDS if str(retake.get(field) or "").strip()) / len(RETAKE_FIELDS), 3), "subtitle_available": bool(subtitle_items), "needs_human_review": False, "warnings": []},
        }
        write_json(desc_path, description)
        description_index.append({"segment_index": new_index, "description_path": str(desc_path), "subtitle_path": str(srt_path), "time": description["time"], "title": title, "source_detail_segment_indices": indices})

    write_json(paths.meta_dir / f"scheme_{target}_segments.json", {"scheme": target, "items": scheme_rows, "recomposed_by": TOOL_NAME, "instruction": str(args.instruction)})
    write_json(paths.meta_dir / f"scheme_{target}_segment_descriptions.json", {"scheme": target, "items": description_index, "recomposed_by": TOOL_NAME, "instruction": str(args.instruction)})
    write_text(paths.storyboards_dir / f"scheme_{target}_storyboard.md", render_storyboard(target, scheme_rows))
    if target == str(args.default_scheme):
        write_json(paths.meta_dir / "fine_logical_segments.json", {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "default_scheme": target, "video_duration": duration, "items": scheme_rows})
    coverage = recompute_coverage(paths.meta_dir, target, scheme_rows, duration)
    update_timeline_coverage(paths, target, coverage)
    export_result = None
    if not bool(args.no_export):
        video_path = load_video_path(paths.meta_dir, args.video)
        export_result = export_target_scheme(paths, target, scheme_rows, video_path, str(args.clip_mode), bool(args.overwrite))
    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed" if coverage["status"] == "passed" else "failed",
        "workspace": str(paths.workspace),
        "target": target,
        "opencrew_task_id": int(args.task_id),
        "model": config.model,
        "opencode_session_id": config.session_id,
        "counts": {"detail_source_segments": len(detail_segments), "target_segments": len(scheme_rows)},
        "coverage_status": coverage["status"],
        "export": export_result,
        "outputs": {
            "segments": str(paths.meta_dir / f"scheme_{target}_segments.json"),
            "descriptions": str(paths.meta_dir / f"scheme_{target}_segment_descriptions.json"),
            "description_dir": str(desc_dir),
            "subtitle_dir": str(sub_dir),
            "storyboard": str(paths.storyboards_dir / f"scheme_{target}_storyboard.md"),
            "scheme_dir": export_result.get("scheme_dir") if export_result else None,
            "scheme_manifest": export_result.get("manifest") if export_result else None,
        },
    }
    write_json(paths.meta_dir / "17_detail_scheme_recomposer_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recompose detail scheme into balanced or summary scheme without rerunning upstream video analysis.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--target", choices=["balanced", "summary"], required=True)
    parser.add_argument("--instruction", required=True, help="Prompt describing how to group detail segments into the target scheme.")
    parser.add_argument("--default-scheme", choices=["detail", "balanced", "summary"], default="balanced")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--video", help="Optional source video path. If omitted, read from metadata.")
    parser.add_argument("--clip-mode", choices=["virtual", "copy", "reencode"], default="virtual", help="virtual writes start/end metadata only; copy/reencode export physical mp4 clips.")
    parser.add_argument("--overwrite", action="store_true", default=True, help="Overwrite the target scheme folder files. Enabled by default for closed-loop recomposition.")
    parser.add_argument("--no-overwrite", dest="overwrite", action="store_false")
    parser.add_argument("--no-export", action="store_true", help="Only rewrite target scheme metadata/transcripts; do not export scheme mp4/srt/json package.")
    parser.add_argument("--fresh-session", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = run(args)
    except DependencyError as exc:
        result = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "failed", "error_code": "missing_dependency", "message": str(exc)}
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
