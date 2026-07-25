from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
CONFIG_TABLE = "tool_media_provider_configs"
PLAN_C_DEFAULT_FRAME_SECONDS = 1.0
TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
from opencrew_runtime_secrets import apply_provider_proxy, resolve_secret_value


class ToolError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "")) or "item"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def now_ms() -> int:
    return int(time.time() * 1000)


def rel(workspace: Path, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(str(path))
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def resolve_workspace_path(workspace: Path, path_value: str) -> Path:
    path = Path(str(path_value or "")).expanduser()
    return path if path.is_absolute() else workspace / path


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def resolve_database_url(args: argparse.Namespace | None = None) -> str:
    env_name = str(getattr(args, "database_url_env", "") or DEFAULT_DATABASE_URL_ENV)
    explicit = str(getattr(args, "database_url", "") or "")
    return explicit or os.environ.get(env_name) or os.environ.get("DATABASE_URL") or DEFAULT_OPENCREW_DATABASE_URL


def postgres_connect(database_url: str) -> Any:
    try:
        import psycopg  # type: ignore
    except Exception as exc:
        raise ToolError("PostgreSQL driver psycopg is not available") from exc
    return psycopg.connect(normalize_database_url(database_url))


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def load_active_media_config(database_url: str, kind: str) -> dict[str, str]:
    env_provider = os.environ.get(f"OPENCREW_{kind.upper()}_PROVIDER", "").strip()
    env_model = os.environ.get(f"OPENCREW_{kind.upper()}_MODEL", "").strip()
    env_key = os.environ.get(f"OPENCREW_{kind.upper()}_API_KEY", "").strip()
    if env_provider and env_model and env_key:
        return {"kind": kind, "provider": env_provider, "model": env_model, "api_key": env_key}
    conn = postgres_connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
SELECT provider, model, api_key_ref, api_key_ciphertext
FROM {CONFIG_TABLE}
WHERE kind = %s AND active = TRUE AND enabled = TRUE
LIMIT 1
""", (kind,))
            row = cursor.fetchone()
        if not row:
            raise ToolError(f"No active {kind} model is configured")
        provider, model, api_key_ref, legacy_key = [decode_text(item).strip() for item in row]
        api_key = resolve_secret_value(api_key_ref, legacy_key)
        if not provider or not model or not api_key:
            raise ToolError(f"Active {kind} config is incomplete")
        apply_provider_proxy(provider)
        return {"kind": kind, "provider": provider, "model": model, "api_key": api_key}
    finally:
        conn.close()


def post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def get_json_request(url: str, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def download_binary(url: str, output_path: Path, headers: dict[str, str], timeout: int = 300) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        output_path.write_bytes(res.read())


def first_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("url", "video_url", "download_url", "uri"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for value in payload.values():
            found = first_url(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = first_url(item)
            if found:
                return found
    return ""


def find_ffmpeg() -> str:
    for candidate in (Path("OpenCrew/ToolLibrary/vendor/static_ffmpeg/darwin_arm64/ffmpeg").resolve(), Path("OpenCrew/.bin/ffmpeg").resolve()):
        if candidate.exists():
            return str(candidate)
    return "ffmpeg"


def find_ffprobe() -> str:
    for candidate in (Path("OpenCrew/ToolLibrary/vendor/static_ffmpeg/darwin_arm64/ffprobe").resolve(), Path("OpenCrew/.bin/ffprobe").resolve()):
        if candidate.exists():
            return str(candidate)
    return "ffprobe"


def run_command(command: list[str], timeout: int = 900) -> dict[str, Any]:
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
    if result.returncode != 0:
        raise ToolError(f"Command failed: {' '.join(command)}\nSTDOUT: {result.stdout[-2000:]}\nSTDERR: {result.stderr[-3000:]}")
    return {"returncode": result.returncode, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]}


def ffprobe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run([find_ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], check=True, capture_output=True, text=True, timeout=20)
        return round(float(result.stdout.strip()), 3)
    except Exception:
        return None


def ffprobe_stream_count(path: Path) -> int:
    try:
        result = subprocess.run([find_ffprobe(), "-v", "error", "-show_entries", "stream=index", "-of", "json", str(path)], check=True, capture_output=True, text=True, timeout=20)
        return len(json.loads(result.stdout).get("streams") or [])
    except Exception:
        return 0


def load_plan(workspace: Path, input_name: str) -> dict[str, Any]:
    path = workspace / input_name
    if not path.exists():
        raise ToolError(f"missing shot plan: {path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ToolError(f"shot plan must be an object: {path}")
    return payload


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("shots", []) if isinstance(item, dict)]


def shot_id_of(shot: dict[str, Any]) -> str:
    return str(shot.get("shot_id") or shot.get("id") or "")


def scene_id_of(mark: dict[str, Any]) -> str:
    return str(mark.get("scene_mark_id") or mark.get("id") or "")


def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    ref = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    return [item for item in ref.get("scene_marks", []) if isinstance(item, dict)]


def target_shots(plan: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    wanted = {str(item) for item in getattr(args, "shot_id", []) if str(item)}
    shots = [shot for shot in shot_list(plan) if not wanted or shot_id_of(shot) in wanted]
    if wanted and not shots:
        raise ToolError(f"No shots matched --shot-id: {sorted(wanted)}")
    return shots


def strip_srt_timing(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def spoken_script(shot: dict[str, Any]) -> str:
    for key in ("spoken_script", "script", "srt_text"):
        value = str(shot.get(key) or "").strip()
        if value:
            return strip_srt_timing(value)
    return ""


def scene_text(mark: dict[str, Any], fallback: str = "") -> str:
    for key in ("srt_text", "scene_srt", "text", "subtitle"):
        value = str(mark.get(key) or "").strip()
        if value:
            return value
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    for key in ("srt_text", "summary", "video_prompt", "prompt"):
        value = str(desc.get(key) or "").strip()
        if value:
            return value
    return str(fallback or "").strip()


def scene_prompt(mark: dict[str, Any]) -> str:
    final_prompts = mark.get("final_prompts") if isinstance(mark.get("final_prompts"), dict) else {}
    for key in ("grok_video_prompt", "video_prompt"):
        value = str(final_prompts.get(key) or "").strip()
        if value:
            return value
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    for key in ("video_prompt", "motion_prompt", "prompt", "summary"):
        value = str(desc.get(key) or "").strip()
        if value:
            return value
    return scene_text(mark)


def variant_scene_dir(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return workspace / "Assets" / variant_id / safe_name(shot_id) / safe_name(scene_mark_id)


def variant_shot_dir(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return workspace / "Assets" / variant_id / safe_name(shot_id)


def canonical_scene_image_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "first.png"


def scene_asset_manifest_path(workspace: Path, shot_id: str, scene_mark_id: str, variant_id: str = "variant_001") -> Path:
    return variant_scene_dir(workspace, shot_id, scene_mark_id, variant_id) / "asset_manifest.json"


def shot_tts_dir(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return variant_shot_dir(workspace, shot_id, variant_id) / "tts"


def canonical_locked_tts_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "locked.wav"


def canonical_shot_srt_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "shot.srt"


def canonical_shot_tts_timeline_path(workspace: Path, shot_id: str, variant_id: str = "variant_001") -> Path:
    return shot_tts_dir(workspace, shot_id, variant_id) / "shot_tts_timeline.locked.json"


def plan_c_shot_video_path(workspace: Path, shot_id: str) -> Path:
    return variant_shot_dir(workspace, shot_id) / "plan_c.mp4"


def plan_c_shot_srt_path(workspace: Path, shot_id: str) -> Path:
    return variant_shot_dir(workspace, shot_id) / "plan_c.srt"


def plan_c_alignment_path(workspace: Path, shot_id: str) -> Path:
    return variant_shot_dir(workspace, shot_id) / "plan_c_alignment.json"


def plan_c_reports_dir(workspace: Path, shot_id: str) -> Path:
    return variant_shot_dir(workspace, shot_id) / "reports"


def plan_c_report_path(workspace: Path, shot_id: str, filename: str) -> Path:
    return plan_c_reports_dir(workspace, shot_id) / filename


def plan_c_scene_video_plan_path(workspace: Path, shot_id: str, scene_id: str) -> Path:
    return variant_scene_dir(workspace, shot_id, scene_id) / "plan_c_video_plan.json"


def plan_c_scene_r2v_path(workspace: Path, shot_id: str, scene_id: str) -> Path:
    return variant_scene_dir(workspace, shot_id, scene_id) / "plan_c_r2v.mp4"


def plan_c_scene_retime_path(workspace: Path, shot_id: str, scene_id: str) -> Path:
    return variant_scene_dir(workspace, shot_id, scene_id) / "plan_c_retime.json"


def plan_c_scene_retimed_video_path(workspace: Path, shot_id: str, scene_id: str) -> Path:
    return variant_scene_dir(workspace, shot_id, scene_id) / "plan_c_retimed.mp4"


def plan_c_batch_dir(workspace: Path, shot_id: str) -> Path:
    return variant_shot_dir(workspace, shot_id) / "reports" / "plan_c_batches"


def plan_c_batch_plan_path(workspace: Path, shot_id: str) -> Path:
    return plan_c_batch_dir(workspace, shot_id) / "batch_plan.json"


def plan_c_batch_video_path(workspace: Path, shot_id: str, batch_id: str) -> Path:
    return plan_c_batch_dir(workspace, shot_id) / f"{safe_name(batch_id)}.mp4"


def plan_c_visual_concat_path(workspace: Path, shot_id: str) -> Path:
    return plan_c_batch_dir(workspace, shot_id) / "visual_concat.mp4"


def plan_c_frame_seconds(args: argparse.Namespace) -> float:
    return max(0.1, safe_float(getattr(args, "plan_c_frame_seconds", PLAN_C_DEFAULT_FRAME_SECONDS), PLAN_C_DEFAULT_FRAME_SECONDS))


def redact_config(config: dict[str, str]) -> dict[str, Any]:
    return {key: (len(value) if key == "api_key" else value) for key, value in config.items()}


def timeline_entries(workspace: Path, shot: dict[str, Any]) -> list[dict[str, Any]]:
    shot_id = shot_id_of(shot)
    timeline_path = canonical_shot_tts_timeline_path(workspace, shot_id)
    pages = []
    if timeline_path.exists():
        timeline = read_json(timeline_path)
        pages = [item for item in timeline.get("image_pages") or [] if isinstance(item, dict)]
    if pages:
        return [{"shot_id": shot_id, "scene_mark_id": str(item.get("scene_mark_id") or ""), "image": str(item.get("image") or ""), "text": str(item.get("text") or ""), "locked_start": safe_float(item.get("start")), "locked_end": safe_float(item.get("end")), "locked_duration": safe_float(item.get("duration")), "scene_prompt": str(item.get("scene_prompt") or item.get("text") or "")} for item in pages]
    marks = scene_marks_for_shot(shot)
    duration = safe_float(shot.get("duration"), 1.0)
    step = duration / max(1, len(marks))
    rows = []
    for index, mark in enumerate(marks):
        scene_id = scene_id_of(mark)
        start = round(index * step, 3)
        end = round(duration if index == len(marks) - 1 else (index + 1) * step, 3)
        rows.append({"shot_id": shot_id, "scene_mark_id": scene_id, "image": rel(workspace, canonical_scene_image_path(workspace, shot_id, scene_id)), "text": scene_text(mark, spoken_script(shot)), "locked_start": start, "locked_end": end, "locked_duration": round(max(0.1, end - start), 3), "scene_prompt": scene_prompt(mark)})
    return rows


def readiness_for_shot(workspace: Path, shot: dict[str, Any]) -> dict[str, Any]:
    shot_id = shot_id_of(shot)
    blockers: list[str] = []
    warnings: list[str] = []
    locked_tts = canonical_locked_tts_path(workspace, shot_id)
    timeline_path = canonical_shot_tts_timeline_path(workspace, shot_id)
    srt_path = canonical_shot_srt_path(workspace, shot_id)
    entries = timeline_entries(workspace, shot) if timeline_path.exists() else []
    if not locked_tts.exists():
        blockers.append("missing_locked_tts")
    if not timeline_path.exists():
        blockers.append("missing_shot_tts_timeline")
    if not srt_path.exists():
        blockers.append("missing_shot_srt")
    if not entries:
        blockers.append("missing_timeline_scene_entries")
    for entry in entries:
        scene_id = str(entry.get("scene_mark_id") or "")
        image = resolve_workspace_path(workspace, str(entry.get("image") or ""))
        if not image.exists():
            blockers.append(f"{scene_id}: missing_scene_image")
        if not scene_asset_manifest_path(workspace, shot_id, scene_id).exists():
            blockers.append(f"{scene_id}: missing_asset_manifest")
        if not str(entry.get("text") or "").strip():
            blockers.append(f"{scene_id}: missing_scene_srt_text")
        if not str(entry.get("scene_prompt") or "").strip():
            warnings.append(f"{scene_id}: missing_scene_video_prompt_using_srt_fallback")
    payload = {"shot_id": shot_id, "status": "ready" if not blockers else "blocked", "locked_tts": rel(workspace, locked_tts) if locked_tts.exists() else "", "timeline": rel(workspace, timeline_path) if timeline_path.exists() else "", "shot_srt": rel(workspace, srt_path) if srt_path.exists() else "", "scene_count": len(entries), "scenes": entries, "warnings": warnings, "blocking_errors": blockers, "mode": "plan_c_equal_frame_batch_r2v_srt_retime", "default_frame_seconds": PLAN_C_DEFAULT_FRAME_SECONDS}
    return payload


def readiness_check(workspace: Path, plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    results = [readiness_for_shot(workspace, shot) for shot in target_shots(plan, args)]
    blockers = [f"{item['shot_id']}: {error}" for item in results for error in (item.get("blocking_errors") or [])]
    payload = {"status": "completed_with_blockers" if blockers else "completed", "tool_id": "11_01_Shot_PlanC_ReadinessCheck", "blocking_errors": blockers, "results": results}
    for item in results:
        write_json(plan_c_report_path(workspace, str(item.get("shot_id") or ""), "plan_c_11_01_readiness_check.json"), {**payload, "results": [item]})
    return payload


def video_capability(config: dict[str, str]) -> dict[str, Any]:
    provider = str(config.get("provider") or "")
    model = str(config.get("model") or "")
    if provider == "xai":
        return {"provider": provider, "model": model, "min_duration": 2, "max_duration": 6, "duration_step": 1, "max_reference_images": 4}
    return {"provider": provider, "model": model, "min_duration": 3, "max_duration": 6, "duration_step": 1, "max_reference_images": 1}


def batch_counts(scene_count: int, frame_seconds: float, capability: dict[str, Any]) -> list[int]:
    if scene_count <= 0:
        return []
    min_duration = safe_float(capability.get("min_duration"), 2.0)
    max_duration = safe_float(capability.get("max_duration"), 6.0)
    max_refs = max(1, int(safe_float(capability.get("max_reference_images"), 1)))
    max_refs_by_duration = max(1, int(math.floor(max_duration / frame_seconds)))
    preferred = min(max_refs, max_refs_by_duration, max(1, int(math.ceil(min_duration / frame_seconds))))
    counts, remaining = [], scene_count
    while remaining > 0:
        count = min(preferred, remaining)
        counts.append(count)
        remaining -= count
    return counts


def build_batches(workspace: Path, shot: dict[str, Any], frame_seconds: float, capability: dict[str, Any]) -> list[dict[str, Any]]:
    shot_id = shot_id_of(shot)
    scenes = timeline_entries(workspace, shot)
    counts = batch_counts(len(scenes), frame_seconds, capability)
    batches, offset = [], 0
    min_duration = safe_float(capability.get("min_duration"), 2.0)
    for batch_index, count in enumerate(counts, start=1):
        batch_scenes = scenes[offset:offset + count]
        offset += count
        sequence_duration = round(len(batch_scenes) * frame_seconds, 3)
        provider_duration = int(math.ceil(max(min_duration, sequence_duration)))
        batch_id = f"{shot_id}_batch_{batch_index:03d}"
        timing_plan = []
        for scene_index, scene in enumerate(batch_scenes, start=1):
            start = round((scene_index - 1) * frame_seconds, 3)
            end = round(scene_index * frame_seconds, 3)
            timing_plan.append({"scene_mark_id": scene.get("scene_mark_id"), "reference_image_index": scene_index, "target_visual_start": start, "target_visual_end": end, "target_visual_duration": round(frame_seconds, 3), "locked_tts_start": scene.get("locked_start"), "locked_tts_end": scene.get("locked_end"), "locked_tts_duration": scene.get("locked_duration")})
        batches.append({"batch_id": batch_id, "batch_index": batch_index, "frame_seconds": round(frame_seconds, 3), "scene_mark_ids": [str(scene.get("scene_mark_id") or "") for scene in batch_scenes], "scenes": batch_scenes, "reference_images": [scene.get("image") for scene in batch_scenes], "sequence_duration": sequence_duration, "provider_duration": provider_duration, "hold_tail_duration": round(max(0.0, provider_duration - sequence_duration), 3), "timing_plan": timing_plan})
    return batches


def batch_prompt(shot: dict[str, Any], batch: dict[str, Any]) -> str:
    frame_seconds = safe_float(batch.get("frame_seconds"), PLAN_C_DEFAULT_FRAME_SECONDS)
    timing_lines = ["- {start:.2f}s-{end:.2f}s: reference image {ref} must be the dominant visual for {scene}.".format(start=safe_float(item.get("target_visual_start")), end=safe_float(item.get("target_visual_end")), ref=int(safe_float(item.get("reference_image_index"), 0)), scene=item.get("scene_mark_id") or "scene") for item in batch.get("timing_plan") or []]
    scene_lines = []
    for index, scene in enumerate(batch.get("scenes") or [], start=1):
        visual_prompt = " ".join(str(scene.get("scene_prompt") or scene.get("text") or "").split())[:360]
        scene_lines.append(f"S{index} {scene.get('scene_mark_id')}: narration context: \"{scene.get('text')}\"; visual: {visual_prompt}")
    hold_tail = safe_float(batch.get("hold_tail_duration"), 0.0)
    hold_instruction = f" After the last timed image segment, hold the last reference image unchanged for the remaining {hold_tail:.2f}s." if hold_tail > 0.01 else ""
    return "Create one realistic vertical 9:16 R2V clip from the ordered reference images. Follow image order exactly; no skips and no reordering. Each reference image must occupy exactly {frame_seconds:.2f}s with clear visual ownership of its time window.{hold_instruction} Use subtle natural motion, stable camera, and clean transitions only at the requested boundaries. Do not generate visible subtitles, captions, logos, watermarks, UI text, readable package text, medical claims, authority claims, or before-and-after claims. Generated audio is not needed and will be discarded. Timing will be sliced deterministically in post-production.\nShot ID: {shot_id}\nBatch ID: {batch_id}\nTarget duration: {duration} seconds\n\nEqual frame timing:\n{timing}\n\nScene details:\n{scenes}".format(frame_seconds=frame_seconds, hold_instruction=hold_instruction, shot_id=shot_id_of(shot), batch_id=batch.get("batch_id"), duration=batch.get("provider_duration"), timing="\n".join(timing_lines), scenes="\n".join(scene_lines))


def generate_xai_video(config: dict[str, str], prompt: str, output_path: Path, reference_images: list[Path], duration: float) -> dict[str, Any]:
    seconds = max(2, min(int(math.ceil(duration)), 6))
    payload: dict[str, Any] = {"model": config["model"], "prompt": prompt, "duration": seconds, "aspect_ratio": "9:16", "resolution": "720p", "reference_images": []}
    for image_path in reference_images:
        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload["reference_images"].append({"url": f"data:{mime};base64,{encoded}"})
    started = post_json_request("https://api.x.ai/v1/videos/generations", payload, {"Authorization": f"Bearer {config['api_key']}"}, timeout=120)
    video_id = str(started.get("request_id") or started.get("id") or ((started.get("data") or {}).get("id") if isinstance(started.get("data"), dict) else "") or "")
    video_url = first_url(started)
    deadline = time.time() + 900
    while not video_url and video_id and time.time() < deadline:
        polled = get_json_request(f"https://api.x.ai/v1/videos/{urllib.parse.quote(video_id, safe='')}", {"Authorization": f"Bearer {config['api_key']}"}, timeout=120)
        status = str(polled.get("status") or "").lower()
        if status in {"failed", "failure", "canceled", "cancelled"}:
            raise ToolError(f"xAI video generation failed: {json.dumps(polled, ensure_ascii=False)[:1200]}")
        if status == "done" or first_url(polled):
            video_url = first_url(polled)
            break
        time.sleep(5)
    if not video_url:
        raise ToolError("xAI video generation completed without a downloadable video URL")
    download_binary(video_url, output_path, {"Authorization": f"Bearer {config['api_key']}"})
    return {"provider": config["provider"], "model": config["model"], "duration": seconds, "reference_image_count": len(reference_images), "output_path": rel(output_path.parent.parent.parent.parent.parent if False else Path.cwd(), output_path), "video_url": video_url}


def per_scene_r2v_generate(workspace: Path, plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    try:
        config = load_active_media_config(resolve_database_url(args), "video")
        config_error = ""
    except Exception as exc:
        config, config_error = {"provider": "", "model": "", "api_key": ""}, str(exc)
    results, blockers = [], []
    frame_seconds = plan_c_frame_seconds(args)
    for shot in target_shots(plan, args):
        shot_id = shot_id_of(shot)
        readiness = readiness_for_shot(workspace, shot)
        shot_blockers = list(readiness.get("blocking_errors") or [])
        if config_error:
            shot_blockers.append(f"video_config_unavailable: {config_error}")
        capability = video_capability(config)
        batches, batch_results = build_batches(workspace, shot, frame_seconds, capability), []
        for batch in batches:
            batch_id = str(batch.get("batch_id") or "")
            output_video = plan_c_batch_video_path(workspace, shot_id, batch_id)
            prompt = batch_prompt(shot, batch)
            batch_payload = {**batch, "prompt": prompt, "output_video": rel(workspace, output_video), "provider": config.get("provider"), "model": config.get("model")}
            if output_video.exists() and ffprobe_duration(output_video) and not getattr(args, "force", False):
                batch_results.append({**batch_payload, "status": "completed", "source": "existing_batch_video", "duration": ffprobe_duration(output_video)})
                continue
            if shot_blockers:
                batch_results.append({**batch_payload, "status": "blocked", "blocking_errors": shot_blockers})
                continue
            if str(config.get("provider") or "") != "xai":
                reason = f"unsupported_plan_c_direct_video_provider: {config.get('provider')}/{config.get('model') or ''}"
                shot_blockers.append(reason)
                batch_results.append({**batch_payload, "status": "blocked", "blocking_errors": [reason]})
                continue
            reference_images = [resolve_workspace_path(workspace, str(path)) for path in (batch.get("reference_images") or [])]
            missing_images = [rel(workspace, path) for path in reference_images if not path.exists()]
            if missing_images:
                reason = f"missing_reference_images: {missing_images}"
                shot_blockers.append(f"{batch_id}: {reason}")
                batch_results.append({**batch_payload, "status": "blocked", "blocking_errors": [reason]})
                continue
            started = time.time()
            generation = generate_xai_video(config, prompt, output_video, reference_images, safe_float(batch.get("provider_duration"), 2.0))
            batch_results.append({**batch_payload, "status": "completed", "generation": generation, "duration": ffprobe_duration(output_video), "elapsed_seconds": round(time.time() - started, 3)})
        scene_plan = {"shot_id": shot_id, "status": "completed_with_blockers" if shot_blockers else "completed", "mode": "plan_c_equal_frame_batch_r2v", "frame_seconds": round(frame_seconds, 3), "provider_capability": capability, "batch_scene_counts": [len(batch.get("scenes") or []) for batch in batch_results], "batches": batch_results, "blocking_errors": shot_blockers, "video_config": redact_config(config)}
        write_json(plan_c_batch_plan_path(workspace, shot_id), scene_plan)
        for batch in batch_results:
            for scene in [item for item in (batch.get("scenes") or []) if isinstance(item, dict)]:
                scene_id = str(scene.get("scene_mark_id") or "")
                if scene_id:
                    write_json(plan_c_scene_video_plan_path(workspace, shot_id, scene_id), scene_plan)
        blockers.extend(f"{shot_id}: {item}" for item in shot_blockers)
        results.append(scene_plan)
    payload = {"status": "completed_with_blockers" if blockers else "completed", "tool_id": "11_02_Shot_PlanC_PerSceneR2VGenerate", "blocking_errors": blockers, "results": results}
    for item in results:
        write_json(plan_c_report_path(workspace, str(item.get("shot_id") or ""), "plan_c_11_02_per_scene_r2v_generate.json"), {**payload, "results": [item]})
    return payload


def srt_retime_compose(workspace: Path, plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    results, blockers = [], []
    for shot in target_shots(plan, args):
        shot_id = shot_id_of(shot)
        scene_plan_path = plan_c_batch_plan_path(workspace, shot_id)
        if not scene_plan_path.exists():
            blockers.append(f"{shot_id}: missing_plan_c_video_plan")
            results.append({"shot_id": shot_id, "status": "blocked", "blocking_errors": ["missing_plan_c_video_plan"]})
            continue
        scene_plan = read_json(scene_plan_path)
        frame_seconds = safe_float(scene_plan.get("frame_seconds"), plan_c_frame_seconds(args))
        source_scenes = []
        for batch in [item for item in (scene_plan.get("batches") or []) if isinstance(item, dict)]:
            if str(batch.get("status") or "") != "completed":
                continue
            batch_video = resolve_workspace_path(workspace, str(batch.get("output_video") or ""))
            for timing in [item for item in (batch.get("timing_plan") or []) if isinstance(item, dict)]:
                scene_id = str(timing.get("scene_mark_id") or "")
                scene = next((item for item in (batch.get("scenes") or []) if isinstance(item, dict) and str(item.get("scene_mark_id") or "") == scene_id), {})
                source_video = plan_c_scene_r2v_path(workspace, shot_id, scene_id)
                run_command([find_ffmpeg(), "-y", "-i", str(batch_video), "-ss", f"{safe_float(timing.get('target_visual_start'), 0.0):.3f}", "-t", f"{safe_float(timing.get('target_visual_duration'), frame_seconds):.3f}", "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,format=yuv420p", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart", str(source_video)], timeout=300)
                source_scenes.append({**scene, "status": "completed", "batch_id": batch.get("batch_id"), "source_video": rel(workspace, source_video)})
        retimed_videos, quality_rows = [], []
        for scene in source_scenes:
            scene_id = str(scene.get("scene_mark_id") or "")
            source_video = resolve_workspace_path(workspace, str(scene.get("source_video") or ""))
            source_duration = ffprobe_duration(source_video) if source_video.exists() else None
            target_duration = max(0.1, safe_float(scene.get("locked_duration"), frame_seconds))
            if not source_duration:
                blockers.append(f"{shot_id}: {scene_id}: missing_or_unreadable_scene_video")
                continue
            retimed_video = plan_c_scene_retimed_video_path(workspace, shot_id, scene_id)
            setpts_ratio = target_duration / source_duration
            run_command([find_ffmpeg(), "-y", "-i", str(source_video), "-vf", f"setpts={setpts_ratio:.8f}*PTS,scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,format=yuv420p", "-t", f"{target_duration:.3f}", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart", str(retimed_video)], timeout=300)
            row = {"scene_mark_id": scene_id, "text": scene.get("text") or "", "source_video": rel(workspace, source_video), "retimed_video": rel(workspace, retimed_video), "target_duration": round(target_duration, 3), "actual_duration": ffprobe_duration(retimed_video), "locked_start": scene.get("locked_start"), "locked_end": scene.get("locked_end"), "speed_ratio": round(source_duration / target_duration, 6), "setpts_ratio": round(setpts_ratio, 6)}
            row["warnings"] = [] if 0.65 <= safe_float(row["speed_ratio"]) <= 1.8 else ["retime_ratio_outside_safe_range"]
            write_json(plan_c_scene_retime_path(workspace, shot_id, scene_id), row)
            quality_rows.append(row)
            retimed_videos.append(retimed_video)
        if not retimed_videos:
            results.append({"shot_id": shot_id, "status": "blocked", "blocking_errors": ["missing_retimed_videos"]})
            continue
        concat_path = plan_c_batch_dir(workspace, shot_id) / "scene_video_concat.txt"
        write_text(concat_path, "\n".join("file '" + str(path).replace("'", "'\\''") + "'" for path in retimed_videos) + "\n")
        visual_concat = plan_c_visual_concat_path(workspace, shot_id)
        run_command([find_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", "-movflags", "+faststart", str(visual_concat)], timeout=1200)
        shot_srt = canonical_shot_srt_path(workspace, shot_id)
        if shot_srt.exists():
            write_text(plan_c_shot_srt_path(workspace, shot_id), shot_srt.read_text(encoding="utf-8"))
        manifest = {"shot_id": shot_id, "status": "completed", "mode": "plan_c_equal_frame_batch_r2v_srt_retime", "scene_plan": rel(workspace, scene_plan_path), "visual_concat": rel(workspace, visual_concat), "duration": ffprobe_duration(visual_concat), "scene_count": len(quality_rows), "warnings": [f"{row.get('scene_mark_id')}: speed_ratio={row.get('speed_ratio')}" for row in quality_rows if row.get("warnings")], "scenes": quality_rows, "generated_at": now_ms()}
        write_json(plan_c_alignment_path(workspace, shot_id), manifest)
        results.append(manifest)
    payload = {"status": "completed_with_blockers" if blockers else "completed", "tool_id": "11_03_Shot_PlanC_SRTRetimeCompose", "blocking_errors": blockers, "results": results}
    for item in results:
        write_json(plan_c_report_path(workspace, str(item.get("shot_id") or ""), "plan_c_11_03_srt_retime_compose.json"), {**payload, "results": [item]})
    return payload


def ffmpeg_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def final_compose(workspace: Path, plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    results, blockers = [], []
    for shot in target_shots(plan, args):
        shot_id = shot_id_of(shot)
        alignment = plan_c_alignment_path(workspace, shot_id)
        visual_concat = plan_c_visual_concat_path(workspace, shot_id)
        locked_tts = canonical_locked_tts_path(workspace, shot_id)
        srt_path = plan_c_shot_srt_path(workspace, shot_id)
        output = plan_c_shot_video_path(workspace, shot_id)
        shot_blockers = []
        if not alignment.exists(): shot_blockers.append("missing_plan_c_alignment")
        if not visual_concat.exists(): shot_blockers.append("missing_plan_c_visual_concat")
        if not locked_tts.exists(): shot_blockers.append("missing_locked_tts")
        if not srt_path.exists(): shot_blockers.append("missing_plan_c_srt")
        if shot_blockers:
            blockers.extend(f"{shot_id}: {item}" for item in shot_blockers)
            results.append({"shot_id": shot_id, "status": "blocked", "blocking_errors": shot_blockers, "output_video": rel(workspace, output)})
            continue
        video_filter = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,format=yuv420p"
        if srt_path.exists() and srt_path.stat().st_size > 0:
            video_filter += f",subtitles='{ffmpeg_filter_path(srt_path)}'"
        run_command([find_ffmpeg(), "-y", "-i", str(visual_concat), "-i", str(locked_tts), "-vf", video_filter, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(output)], timeout=900)
        manifest = {"shot_id": shot_id, "status": "completed", "mode": "plan_c_equal_frame_batch_r2v_srt_retime", "retime_manifest": rel(workspace, alignment), "visual_concat": rel(workspace, visual_concat), "locked_tts": rel(workspace, locked_tts), "shot_srt": rel(workspace, srt_path), "output_video": rel(workspace, output), "duration": ffprobe_duration(output), "stream_count": ffprobe_stream_count(output), "generated_at": now_ms()}
        results.append(manifest)
    payload = {"status": "completed_with_blockers" if blockers else "completed", "tool_id": "11_04_Shot_PlanC_FinalCompose", "blocking_errors": blockers, "results": results}
    for item in results:
        write_json(plan_c_report_path(workspace, str(item.get("shot_id") or ""), "plan_c_11_04_final_compose.json"), {**payload, "results": [item]})
    return payload


def parse_common_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--shot-id", action="append", default=[])
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--plan-c-frame-seconds", type=float, default=PLAN_C_DEFAULT_FRAME_SECONDS)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main_entry(tool_name: str, runner: Any) -> None:
    args = parse_common_args(tool_name)
    workspace = args.workspace.expanduser().resolve()
    try:
        plan = load_plan(workspace, args.input)
        result = runner(workspace, plan, args)
        payload = {"tool": tool_name, "tool_version": "1.0.0", "status": result.get("status", "completed"), "workspace": str(workspace), "result": result}
    except Exception as exc:
        payload = {"tool": tool_name, "tool_version": "1.0.0", "status": "failed", "message": str(exc)}
    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] == "failed":
        raise SystemExit(1)
