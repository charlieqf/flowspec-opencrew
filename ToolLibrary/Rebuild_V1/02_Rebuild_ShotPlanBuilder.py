from __future__ import annotations

import argparse
import json
import os
import re
import time
from base64 import b64encode
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOOL_NAME = "RebuildShotPlanBuilder"
TOOL_VERSION = "0.1.0"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
KEYFRAME_MODES = ("merged", "scene_detect_only")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_relative_path(value: str, default_name: str) -> Path:
    raw = value.strip() or default_name
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Path must be workspace-relative: {value}")
    return path


def sibling_path(path: Path, name: str) -> Path:
    return path.parent / name if str(path.parent) != "." else Path(name)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").strip()


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


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
            raise RuntimeError("PostgreSQL driver is not available. Install psycopg[binary] or psycopg2-binary in the OpenCrew runtime.") from exc
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


def fetch_context(database_url: str, task_id: int) -> dict[str, Any]:
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
       s.workspace_dir, s.opencode_session_id, analysis_s.workspace_dir AS analysis_workspace_dir
FROM oc_rebuild_tasks t
JOIN sessions s ON s.id = t.session_id
LEFT JOIN openclip_tasks a ON a.id = t.analysis_task_id
LEFT JOIN sessions analysis_s ON analysis_s.id = a.session_id
WHERE t.id = %s
LIMIT 1
""",
                (task_id,),
            )
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
        if not row:
            raise RuntimeError(f"OC-Rebuild Task #{task_id} not found")
        data = dict(zip(columns, row))
        final_prompt = decode_text(data.get("final_prompt")).strip()
        provider = decode_text(data.get("run_model_provider")).strip()
        model_id = decode_text(data.get("run_model_id")).strip()
        opencode_session_id = decode_text(data.get("opencode_session_id")).strip()
        if not final_prompt:
            raise RuntimeError(f"OC-Rebuild Task #{task_id} has no Final Prompt")
        if not provider or not model_id:
            raise RuntimeError(f"OC-Rebuild Task #{task_id} has no run model configured")
        if not base_url or not username or not password or not opencode_session_id:
            raise RuntimeError("OpenCode connection or bound session is incomplete")
        return {
            "task_id": int(data.get("id") or 0),
            "session_id": int(data.get("session_id") or 0),
            "analysis_task_id": int(data.get("analysis_task_id") or 0) or None,
            "analysis_session_id": int(data.get("analysis_session_id") or 0) or None,
            "analysis_workspace_dir": decode_text(data.get("analysis_workspace_dir")).strip(),
            "workspace_dir": decode_text(data.get("workspace_dir")).strip(),
            "source_package_path": decode_text(data.get("source_package_path")).strip() or "source_package.json",
            "final_prompt": final_prompt,
            "run_model_provider": provider,
            "run_model_id": model_id,
            "opencode_session_id": opencode_session_id,
            "opencode_base_url": base_url,
            "opencode_username": username,
            "opencode_password": password,
        }
    finally:
        conn.close()


def request_json(context: dict[str, Any], method: str, path: str, payload: dict[str, Any] | None = None, query: dict[str, str] | None = None, timeout: int = 120) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    token = b64encode(f"{context['opencode_username']}:{context['opencode_password']}".encode("utf-8")).decode("ascii")
    query_string = f"?{urlencode(query)}" if query else ""
    req = Request(
        f"{context['opencode_base_url']}{path}{query_string}",
        data=data,
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    with urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="ignore")
        return json.loads(raw) if raw else None


def assistant_text(messages: list[dict[str, Any]], started_after: int) -> str:
    for message in reversed(messages):
        info = message.get("info") or {}
        if info.get("role") != "assistant":
            continue
        completed = int(((info.get("time") or {}).get("completed") or 0) or 0)
        if completed < started_after:
            continue
        text = "\n".join(str(part.get("text") or "").strip() for part in (message.get("parts") or []) if part.get("type") == "text").strip()
        if text:
            return text
    return ""


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I).strip()
        stripped = re.sub(r"\s*```$", "", stripped).strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    match = re.search(r"\{.*\}", stripped, re.S)
    if not match:
        raise RuntimeError("Model response did not contain a JSON object")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise RuntimeError("Model response JSON must be an object")
    return parsed


def compact_segment(segment: dict[str, Any]) -> dict[str, Any]:
    retake = segment.get("retake_fields") if isinstance(segment.get("retake_fields"), dict) else {}
    return {
        "segment_id": segment.get("segment_id"),
        "index": segment.get("index"),
        "start": segment.get("start"),
        "end": segment.get("end"),
        "duration": segment.get("duration"),
        "title": segment.get("title"),
        "semantic_role": segment.get("semantic_role"),
        "formula_slot": segment.get("formula_slot"),
        "spoken_script": segment.get("spoken_script"),
        "clip_path": segment.get("clip_path"),
        "subtitle_path": segment.get("subtitle_path"),
        "description_path": segment.get("description_path"),
        "keyframes": segment.get("keyframes") or [],
        "visual_content": retake.get("visual_content") or retake.get("summary") or "",
        "scene": retake.get("scene") or retake.get("main_scene") or "",
        "camera": retake.get("camera") or retake.get("shot_type") or "",
        "retake_notes": retake.get("retake_notes") or "",
        "visual_must_have": retake.get("visual_must_have") or "",
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
            if path not in merged:
                merged[path] = dict(frame)
            else:
                merged[path] = {**dict(frame), **merged[path]}
    return sorted(merged.values(), key=lambda item: (safe_float(item.get("time"), 1_000_000.0), str(item.get("path") or "")))


def is_scene_detect_keyframe(frame: dict[str, Any]) -> bool:
    source = str(frame.get("source") or "").strip()
    path = str(frame.get("path") or "").strip()
    return source == "pyscenedetect" or path.startswith("keyframes/pyscenedetect_scenes/")


def filter_keyframes(keyframes: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "scene_detect_only":
        return [frame for frame in keyframes if is_scene_detect_keyframe(frame)]
    return keyframes


def subtitle_text_for_segment(source_package: dict[str, Any], segment: dict[str, Any], context: dict[str, Any]) -> str:
    subtitle_path = str(segment.get("subtitle_path") or "").strip()
    if not subtitle_path:
        return str(segment.get("spoken_script") or "").strip()
    source_info = source_package.get("source") if isinstance(source_package.get("source"), dict) else {}
    roots = [
        str(context.get("analysis_workspace_dir") or "").strip(),
        str(source_info.get("analysis_workspace") or "").strip(),
        str(source_package.get("workspace") or "").strip(),
    ]
    raw_path = Path(subtitle_path)
    candidates = [raw_path] if raw_path.is_absolute() else [Path(root) / subtitle_path for root in roots if root]
    for path in candidates:
        if path.exists() and path.is_file():
            return read_text(path)
    return str(segment.get("spoken_script") or "").strip()


def build_prompt(context: dict[str, Any], source_package: dict[str, Any], rebuild_intent: dict[str, Any]) -> str:
    segments = [compact_segment(item) for item in (source_package.get("segments") or []) if isinstance(item, dict)]
    payload = {
        "task": {"task_id": context["task_id"], "session_id": context["session_id"], "analysis_task_id": context["analysis_task_id"]},
        "final_prompt": context["final_prompt"],
        "rebuild_intent": rebuild_intent,
        "source_video": source_package.get("video") or {},
        "segments": segments,
    }
    return """你是 OpenClip Rebuild Shot Plan Builder。

请基于用户 Final Prompt、rebuild_intent 和 source_package，为每个 source segment 生成可在前端审阅和编辑的 shot-level rebuild plan。

严格要求：
1. 只输出 JSON 对象，不要解释，不要 Markdown。
2. shots 数量必须等于输入 segments 数量，顺序一致。
3. 每个 shot 必须保留 source_segment_id、source_index、start、end、duration、role、formula_slot 和 reference.clip_path。
4. 每个 shot 必须包含 ui_summary、rebuild_direction、generation_hint、quality_notes。
5. 不要生成 asset task、ComfyUI workflow、render plan 或执行命令。
6. 健康养生内容不得医疗化、不得承诺疗效、不得夸大。

输出 JSON 顶层结构：
{
  "version": 1,
  "tool": "RebuildShotPlanBuilder",
  "task": {"task_id": 0, "session_id": 0, "analysis_task_id": 0},
  "model": {"provider": "", "model": ""},
  "source": {"source_package_path": "source_package.json", "rebuild_intent_path": "rebuild_intent.json", "source_scheme": "detail", "segment_count": 0},
  "variants": [],
  "shots": [],
  "validation": {"status": "passed", "warnings": []}
}

输入上下文：
""" + json.dumps(payload, ensure_ascii=False, indent=2)


def normalize_plan(plan: dict[str, Any], context: dict[str, Any], source_package: dict[str, Any], rebuild_intent: dict[str, Any], keyframe_mode: str, source_package_path: str, rebuild_intent_path: str) -> dict[str, Any]:
    shots = plan.get("shots") if isinstance(plan.get("shots"), list) else []
    segments = [item for item in (source_package.get("segments") or []) if isinstance(item, dict)]
    if len(shots) != len(segments):
        raise RuntimeError(f"Shot plan count mismatch: expected {len(segments)}, got {len(shots)}")
    for index, (shot, segment) in enumerate(zip(shots, segments), start=1):
        if not isinstance(shot, dict):
            raise RuntimeError(f"Shot #{index} must be an object")
        shot.setdefault("shot_id", f"shot_{index:03d}")
        shot.setdefault("source_segment_id", segment.get("segment_id") or f"segment_{index:03d}")
        shot.setdefault("source_index", segment.get("index") or index)
        shot.setdefault("start", segment.get("start"))
        shot.setdefault("end", segment.get("end"))
        shot.setdefault("duration", segment.get("duration"))
        shot.setdefault("role", segment.get("semantic_role") or "")
        shot.setdefault("formula_slot", segment.get("formula_slot") or "")
        reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
        reference["clip_path"] = segment.get("clip_path") or reference.get("clip_path") or ""
        reference["source_video_path"] = segment.get("source_video_path") or reference.get("source_video_path") or ""
        reference["clip_status"] = segment.get("clip_status") or reference.get("clip_status") or ""
        reference["start"] = segment.get("start")
        reference["end"] = segment.get("end")
        reference["duration"] = segment.get("duration")
        reference["subtitle_path"] = reference.get("subtitle_path") or segment.get("subtitle_path") or ""
        reference["description_path"] = reference.get("description_path") or segment.get("description_path") or ""
        reference["resource_session"] = "analysis"
        reference["srt_text"] = subtitle_text_for_segment(source_package, segment, context)
        reference["keyframes"] = filter_keyframes(merge_keyframes(segment.get("keyframes"), reference.get("keyframes")), keyframe_mode)
        for frame in reference["keyframes"]:
            if isinstance(frame, dict):
                frame.setdefault("resource_session", "analysis")
        reference["original_keyframes"] = [dict(frame) for frame in reference["keyframes"] if isinstance(frame, dict)]
        reference["deleted_keyframes"] = []
        shot["reference"] = reference
        if isinstance(shot.get("ui_summary"), str):
            shot["ui_summary"] = {"summary": shot["ui_summary"]}
        else:
            shot.setdefault("ui_summary", {})
        if isinstance(shot.get("rebuild_direction"), str):
            shot["rebuild_direction"] = {"direction": shot["rebuild_direction"]}
        else:
            shot.setdefault("rebuild_direction", {})
        if isinstance(shot.get("generation_hint"), str):
            shot["generation_hint"] = {"hint": shot["generation_hint"]}
        else:
            shot.setdefault("generation_hint", {})
        if isinstance(shot.get("quality_notes"), str):
            shot["quality_notes"] = [shot["quality_notes"]]
        else:
            shot.setdefault("quality_notes", [])
    plan["version"] = 1
    plan["tool"] = TOOL_NAME
    plan["tool_version"] = TOOL_VERSION
    plan["task"] = {
        "task_id": context["task_id"],
        "session_id": context["session_id"],
        "analysis_task_id": context["analysis_task_id"],
        "analysis_session_id": context["analysis_session_id"],
    }
    plan["model"] = {"provider": context["run_model_provider"], "model": context["run_model_id"]}
    plan["source"] = {
        "source_package_path": source_package_path,
        "rebuild_intent_path": rebuild_intent_path,
        "source_scheme": source_package.get("source_scheme") or "detail",
        "segment_count": len(segments),
    }
    plan["variants"] = ((rebuild_intent.get("batch") or {}).get("variants") or []) if isinstance(rebuild_intent.get("batch"), dict) else []
    plan.setdefault("validation", {"status": "passed", "warnings": []})
    return plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build rebuild_shot_plan.json using Task Final Prompt, run model, source_package.json, and rebuild_intent.json.")
    parser.add_argument("--workspace", type=Path, default=None, help="OC-Rebuild task workspace. Defaults to the database-bound workspace for --task-id.")
    parser.add_argument("--task-id", required=True, type=int)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--keyframe-mode", choices=KEYFRAME_MODES, default="scene_detect_only", help="How to write shot reference keyframes: scene_detect_only keeps only PySceneDetect scene frames, merged keeps all sources.")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        database_url = args.database_url or os.environ.get(str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL
        context = fetch_context(database_url, args.task_id)
        workspace = Path(str(context["workspace_dir"])).expanduser().resolve()
        if args.workspace:
            requested_workspace = args.workspace.expanduser().resolve()
            if requested_workspace != workspace:
                raise RuntimeError(f"--workspace does not match OC-Rebuild Task #{args.task_id}: requested={requested_workspace}, database={workspace}")
        source_package_rel = safe_relative_path(str(context.get("source_package_path") or "source_package.json"), "source_package.json")
        rebuild_intent_rel = sibling_path(source_package_rel, "rebuild_intent.json")
        source_package = read_json(workspace / source_package_rel)
        rebuild_intent = read_json(workspace / rebuild_intent_rel)
        started_at = int(time.time() * 1000)
        request_json(
            context,
            "POST",
            f"/session/{context['opencode_session_id']}/prompt_async",
            {
                "parts": [{"type": "text", "text": build_prompt(context, source_package, rebuild_intent)}],
                "model": {"providerID": context["run_model_provider"], "modelID": context["run_model_id"]},
            },
            query={"directory": context["workspace_dir"]},
            timeout=30,
        )
        deadline = time.time() + 300
        response_text = ""
        while time.time() < deadline:
            messages = request_json(context, "GET", f"/session/{context['opencode_session_id']}/message", None, query={"directory": context["workspace_dir"], "limit": "160"}, timeout=30) or []
            response_text = assistant_text(messages, started_at)
            if response_text:
                break
            time.sleep(1)
        if not response_text:
            raise RuntimeError("OpenCode timed out before returning shot plan")
        plan = normalize_plan(extract_json_object(response_text), context, source_package, rebuild_intent, str(args.keyframe_mode), source_package_rel.as_posix(), rebuild_intent_rel.as_posix())
        write_json(workspace / "rebuild_shot_plan.json", plan)
        result = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "completed", "output": "rebuild_shot_plan.json", "shot_count": len(plan["shots"])}
    except Exception as exc:
        result = {"tool": TOOL_NAME, "tool_version": TOOL_VERSION, "status": "failed", "message": str(exc)}
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
