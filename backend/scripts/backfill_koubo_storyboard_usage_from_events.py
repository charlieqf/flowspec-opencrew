#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from opcrew_backend.services.local_usage import LocalUsageRecorder  # noqa: E402
from opcrew_backend.koubo.koubo_storyboard.usage_metering import (  # noqa: E402
    chat_usage_units,
    image_usage_units,
    record_storyboard_usage,
    stable_usage_request_id,
    tts_usage_units,
    video_usage_units,
    voice_clone_usage_units,
)

DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"


def parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def text_value(value: Any, default: str = "") -> str:
    if value in (None, ""):
        value = default
    return str(value or "").strip()


def int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def number_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def audio_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            return round(handle.getnframes() / rate, 3) if rate else 0.0
    except Exception:
        return 0.0


def task_rows(engine: Any, task_ids: list[int]) -> dict[int, dict[str, Any]]:
    placeholders = ", ".join(f":task_{index}" for index, _task_id in enumerate(task_ids))
    params = {f"task_{index}": task_id for index, task_id in enumerate(task_ids)}
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"""
SELECT t.id, t.session_id, t.latest_attempt_id, s.workspace_dir
FROM openclip_tasks t
LEFT JOIN sessions s ON s.id = t.session_id
WHERE t.id IN ({placeholders})
"""
            ),
            params,
        ).mappings().all()
    return {int(row["id"]): dict(row) for row in rows}


def session_events(engine: Any, task_map: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    session_ids = sorted({int(row["session_id"]) for row in task_map.values()})
    if not session_ids:
        return []
    placeholders = ", ".join(f":session_{index}" for index, _session_id in enumerate(session_ids))
    params = {f"session_{index}": session_id for index, session_id in enumerate(session_ids)}
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"""
SELECT id, session_id, kind, payload, created_at
FROM session_events
WHERE session_id IN ({placeholders})
ORDER BY session_id ASC, created_at ASC, id ASC
"""
            ),
            params,
        ).mappings().all()
    return [dict(row) for row in rows]


def task_for_event(event: dict[str, Any], task_by_session: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    payload = parse_json(event.get("payload"))
    payload_task_id = int_value(payload.get("task_id"))
    if payload_task_id:
        return task_by_session.get(int(event["session_id"])) if int(task_by_session.get(int(event["session_id"]), {}).get("id") or 0) == payload_task_id else None
    return task_by_session.get(int(event["session_id"]))


def output_duration_from_payload(task: dict[str, Any], payload: dict[str, Any]) -> float:
    for key in ("duration_seconds", "effective_duration_seconds", "fit_duration", "raw_duration", "duration"):
        value = number_value(payload.get(key))
        if value > 0:
            return value
    workspace = Path(text_value(task.get("workspace_dir")))
    rel = text_value(payload.get("output") or payload.get("raw_output"))
    if workspace and rel:
        return audio_duration_seconds(workspace / rel)
    return 0.0


def record(
    ctx: Any,
    task: dict[str, Any],
    event: dict[str, Any],
    *,
    provider: str,
    model_id: str,
    modality: str,
    step_id: str,
    units: dict[str, Any],
) -> dict[str, Any]:
    request_id = stable_usage_request_id("backfill_event", event["id"], task["id"], provider, model_id, modality)
    return record_storyboard_usage(
        ctx,
        task,
        request_id=request_id,
        provider=provider,
        model_id=model_id,
        modality=modality,
        step_id=step_id,
        units=units,
        started_at=event.get("created_at"),
        finished_at=event.get("created_at"),
    )


def backfill(database_url: str, task_ids: list[int], dry_run: bool = False) -> dict[str, Any]:
    engine = create_engine(database_url, future=True)
    try:
        tasks = task_rows(engine, task_ids)
        task_by_session = {int(row["session_id"]): row for row in tasks.values()}
        ctx = SimpleNamespace(local_usage=LocalUsageRecorder(engine))
        rows = session_events(engine, tasks)
        inserted = 0
        planned = 0
        skipped = 0
        last_user_text: dict[int, str] = {}
        last_assistant_text: dict[int, str] = {}
        examples: list[dict[str, Any]] = []

        for event in rows:
            payload = parse_json(event.get("payload"))
            if text_value(payload.get("local_usage_id")):
                skipped += 1
                continue
            task = task_for_event(event, task_by_session)
            if not task:
                skipped += 1
                continue
            session_id = int(event["session_id"])
            kind = text_value(event.get("kind"))
            result: dict[str, Any] | None = None

            if kind == "user.message":
                last_user_text[session_id] = text_value(payload.get("text"))
                skipped += 1
                continue
            if kind == "assistant.final":
                last_assistant_text[session_id] = text_value(payload.get("text"))
                skipped += 1
                continue

            if kind == "openclip.prompt.generated":
                provider = text_value(payload.get("provider"))
                model_id = text_value(payload.get("model"))
                prompt_kind = text_value(payload.get("prompt_kind"), "final")
                units = chat_usage_units(input_text=last_user_text.get(session_id, ""), output_text=last_assistant_text.get(session_id, ""))
                result = {"provider": provider, "model_id": model_id, "modality": "chat", "step_id": f"openclip.prompt.{prompt_kind}", "units": units}

            elif kind in {"koubo_storyboard.asset_library_agent.image.generated", "koubo_storyboard.clean_image.generated", "koubo_storyboard.host_product_builder.image.generated"}:
                provider = text_value(payload.get("provider"))
                model_id = text_value(payload.get("model"))
                count = int_value(payload.get("generated_count"), 0) or len(payload.get("outputs") or []) or 1
                prompt = text_value(payload.get("effective_prompt") or payload.get("prompt") or payload.get("effective_prompt_preview") or payload.get("prompt_preview"))
                result = {"provider": provider, "model_id": model_id, "modality": "image", "step_id": kind.rsplit(".", 1)[0], "units": image_usage_units(count=count, prompt=prompt, reference_count=payload.get("reference_count"))}

            elif kind == "koubo_storyboard.asset_library_agent.video.provider_call.completed":
                provider = text_value(payload.get("provider"))
                model_id = text_value(payload.get("model"))
                seconds = number_value(payload.get("effective_duration_seconds") or payload.get("duration"))
                prompt = text_value(payload.get("prompt") or payload.get("prompt_preview"))
                refs = int_value(payload.get("reference_count")) or int_value(payload.get("reference_image_count")) + int_value(payload.get("reference_audio_count")) + int_value(payload.get("reference_video_count"))
                result = {"provider": provider, "model_id": model_id, "modality": "video", "step_id": "koubo_storyboard.asset_library_agent.video", "units": video_usage_units(seconds=seconds, prompt=prompt, reference_count=refs)}

            elif kind in {"koubo_storyboard.scene_tts.generated", "analysis_v1.tts.preview.completed"}:
                provider = text_value(payload.get("provider"))
                model_id = text_value(payload.get("model"))
                spoken_text = text_value(payload.get("text") or payload.get("text_preview") or payload.get("prompt_excerpt"))
                prompt = text_value(payload.get("prompt") or payload.get("prompt_preview") or payload.get("prompt_excerpt"))
                duration = output_duration_from_payload(task, payload)
                result = {"provider": provider, "model_id": model_id, "modality": "tts", "step_id": "koubo_storyboard.scene_tts" if kind.startswith("koubo_storyboard") else "analysis_v1.tts.preview", "units": tts_usage_units(spoken_text, prompt=prompt, audio_seconds=duration)}

            elif kind == "analysis_v1.tts.quick_adv.clone_voice" and payload.get("ok") is True and text_value(payload.get("provider")) == "heygen":
                result = {"provider": "heygen", "model_id": "heygen-voice-clone-v3", "modality": "voice_clone", "step_id": "analysis_v1.tts.quick_adv.clone_voice", "units": voice_clone_usage_units()}

            if result is None or not result.get("provider") or not result.get("model_id"):
                skipped += 1
                continue
            planned += 1
            if dry_run:
                examples.append({"event_id": event["id"], "task_id": task["id"], "kind": kind, **result})
                continue
            record_result = record(ctx, task, event, **result)
            if record_result.get("inserted"):
                inserted += 1
            examples.append({"event_id": event["id"], "task_id": task["id"], "kind": kind, **result, "local_usage": record_result})

        return {"ok": True, "task_ids": task_ids, "planned": planned, "inserted": inserted, "skipped": skipped, "dry_run": dry_run, "examples": examples[:20]}
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Koubo Storyboard model usage rows from session_events.")
    parser.add_argument("--database-url", default=os.environ.get("OPENCREW_DATABASE_URL") or DEFAULT_DATABASE_URL)
    parser.add_argument("--task-ids", required=True, help="Comma-separated openclip task ids, e.g. 142,144,146")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    task_ids = [int(item.strip()) for item in args.task_ids.split(",") if item.strip()]
    result = backfill(args.database_url, task_ids, args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
