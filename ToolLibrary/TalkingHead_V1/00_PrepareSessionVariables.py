from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKFLOW_ID = "person_talking_head_v1"
TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
REPORT_DIR = "S1_00_PrepareSessionVariables/Report"
USER_SCRIPT_REL = "SessionContext/Script/user_script.txt"
REFERENCE_SCRIPT_REL = "SessionContext/Script/reference_script.txt"
FINAL_SRT_ITEMS_REL = "SessionOutput/subtitle/final_srt_frame_items.json"
DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
MEDIA_CONFIG_TABLE = "tool_media_provider_configs"
TTS_PROVIDER = "google"
TTS_MODEL = "gemini-3.1-flash-tts-preview"
SCENE_PROFILE_MODEL = "gemini-3.1-flash-image"
WAN_RTV_PROVIDER = "wan"
WAN_RTV_ALIAS_MODEL = "wan2.7-r2v"
WAN_RTV_MODEL = "wan2.7-r2v-2026-06-12"
WAN_RTV_MODELS = {WAN_RTV_ALIAS_MODEL, WAN_RTV_MODEL}
DEFAULT_VOICE_CLONE_PROVIDER = "heygen"


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def text_value(value: Any) -> str:
    return str(value or "").strip()


def parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def resolve_secret_value(api_key_ref: str, legacy_value: str = "") -> str:
    try:
        from opencrew_runtime_secrets import resolve_secret_value as runtime_resolve_secret_value  # type: ignore
        resolved = text_value(runtime_resolve_secret_value(api_key_ref, legacy_value))
        if resolved:
            return resolved
    except Exception:
        pass
    ref = text_value(api_key_ref)
    if ref:
        env_value = text_value(os.environ.get(ref))
        if env_value:
            return env_value
        try:
            from opcrew_backend.services.local_secrets import LocalSecretStore

            data_dir = Path(os.environ.get("OPENCREW_DATA_DIR") or Path.home() / ".opencrew")
            stored = text_value(LocalSecretStore(data_dir).get(ref))
            if stored:
                return stored
        except Exception:
            pass
    return text_value(legacy_value)


def postgres_connect(database_url: str) -> Any:
    normalized_url = normalize_database_url(database_url)
    try:
        import psycopg  # type: ignore

        conn = psycopg.connect(normalized_url, connect_timeout=5)
        conn.execute("SET client_encoding TO 'UTF8'")
        return conn
    except ImportError:
        import psycopg2  # type: ignore

        conn = psycopg2.connect(normalized_url, connect_timeout=5)
        conn.set_client_encoding("UTF8")
        return conn


def fetch_talking_head_task_config(database_url: str, workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    conn = postgres_connect(database_url)
    try:
        query = """
SELECT
  t.id AS task_id,
  t.session_id,
  t.workflow_mode,
  t.status AS task_status,
  s.title,
  s.workspace_dir,
  s.opencode_session_id,
  c.schema_version,
  c.script_creation_mode,
  c.config_json,
  c.updated_at AS config_updated_at
FROM openclip_tasks t
JOIN sessions s ON s.id = t.session_id
JOIN talking_head_task_configs c ON c.task_id = t.id
WHERE s.workspace_dir = %s
LIMIT 1
"""
        with conn.cursor() as cursor:
            cursor.execute(query, (str(workspace),))
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
    finally:
        conn.close()
    if not row:
        raise RuntimeError(f"TalkingHead_V1 database configuration was not found for workspace: {workspace}")
    task = dict(zip(columns, row))
    if text_value(task.get("workflow_mode")) != WORKFLOW_ID:
        raise RuntimeError(f"TalkingHead_V1/00 cannot prepare workflow_mode={task.get('workflow_mode')!r}")
    config = parse_json_dict(task.get("config_json"))
    if not config:
        raise RuntimeError("TalkingHead_V1 database config_json is empty or invalid.")
    return task, config


def write_text_input(workspace: Path, rel_path: str, content: str) -> dict[str, str]:
    target = workspace / rel_path
    if content:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {
            "path": rel_path,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
    if target.exists():
        target.unlink()
    return {"path": "", "sha256": ""}


def default_media_public_config(kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "provider": "",
        "model": "",
        "enabled": False,
        "active": False,
        "api_key_ref": "",
        "has_api_key": False,
        "source": "public_default_missing",
        "extra": {},
        "extra_json": {},
    }


def default_tts_public_config() -> dict[str, Any]:
    return {
        "kind": "tts",
        "provider": TTS_PROVIDER,
        "model": TTS_MODEL,
        "builder": "Builder-G",
        "builder_g_default_model": TTS_MODEL,
        "scene_profile_model": SCENE_PROFILE_MODEL,
        "scene_profile_default_model": SCENE_PROFILE_MODEL,
        "api_key_ref": "google_gemini_tts_key",
        "has_api_key": False,
        "source": "public_default",
    }


def media_config_payload(row: Any, columns: list[str], kind: str) -> dict[str, Any]:
    data = dict(zip(columns, row))
    extra = parse_json_dict(data.get("extra_json"))
    api_key_ref = text_value(data.get("api_key_ref"))
    legacy_key = text_value(data.get("api_key_ciphertext"))
    return {
        "kind": text_value(data.get("kind")) or kind,
        "provider": text_value(data.get("provider")),
        "model": text_value(data.get("model")),
        "enabled": bool(data.get("enabled")),
        "active": bool(data.get("active")),
        "api_key_ref": api_key_ref,
        "has_api_key": bool(resolve_secret_value(api_key_ref, legacy_key)),
        "source": f"postgres:{MEDIA_CONFIG_TABLE}",
        "extra": extra,
        "extra_json": extra,
        "updated_at": text_value(data.get("updated_at")),
    }


def fetch_media_public_config(database_url: str, kind: str, provider: str = "", model: str = "") -> tuple[dict[str, Any], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    try:
        conn = postgres_connect(database_url)
    except Exception as exc:
        warnings.append({"code": f"{kind}_config_database_unavailable", "message": str(exc)})
        return default_media_public_config(kind), warnings
    try:
        query = f"""
SELECT kind, provider, model, api_key_ciphertext, api_key_ref, enabled, active, updated_at, extra_json
FROM {MEDIA_CONFIG_TABLE}
WHERE kind = %s AND enabled = true
"""
        params: list[Any] = [kind]
        if provider:
            query += " AND provider = %s"
            params.append(provider)
        if model:
            query += " AND model = %s"
            params.append(model)
        query += " ORDER BY active DESC, id ASC LIMIT 1"
        with conn.cursor() as cursor:
            cursor.execute(query, tuple(params))
            row = cursor.fetchone()
            columns = [item.name for item in cursor.description] if cursor.description else []
    except Exception as exc:
        warnings.append({"code": f"{kind}_config_query_failed", "message": str(exc)})
        return default_media_public_config(kind), warnings
    finally:
        conn.close()
    if not row:
        warnings.append({"code": f"{kind}_default_config_missing", "message": f"No enabled {kind} provider config was found."})
        return default_media_public_config(kind), warnings
    return media_config_payload(row, columns, kind), warnings


def fetch_tts_public_config(database_url: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    config, warnings = fetch_media_public_config(database_url, "tts", TTS_PROVIDER)
    if not config.get("provider"):
        return default_tts_public_config(), warnings
    extra = dict_value(config.get("extra"))
    return {
        **config,
        "builder": "Builder-G",
        "builder_g_default_model": TTS_MODEL,
        "scene_profile_model": text_value(extra.get("scene_profile_model") or extra.get("builder_g_scene_profile_model")) or SCENE_PROFILE_MODEL,
        "scene_profile_default_model": SCENE_PROFILE_MODEL,
    }, warnings


def talking_head_video_config(default_video_config: dict[str, Any], talking_head: dict[str, Any]) -> dict[str, Any]:
    video_model = dict_value(talking_head.get("video_model"))
    requested_model = text_value(video_model.get("model") or WAN_RTV_MODEL)
    runtime_model = WAN_RTV_MODEL if requested_model in WAN_RTV_MODELS else requested_model
    return {
        **default_video_config,
        "kind": "video",
        "provider": video_model.get("provider") or WAN_RTV_PROVIDER,
        "model": runtime_model,
        "model_alias": text_value(video_model.get("model_alias")) or WAN_RTV_ALIAS_MODEL,
        # The task-level selection becomes this Session's default video
        # model even when the provider row is not the system-wide active row.
        "enabled": True,
        "active": True,
        "source": f"{default_video_config.get('source') or 'database'} + talking_head_video_selection",
    }


def fetch_selected_video_public_config(
    database_url: str,
    selected_video_model: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    provider = text_value(selected_video_model.get("provider")) or WAN_RTV_PROVIDER
    model = text_value(selected_video_model.get("model")) or WAN_RTV_MODEL
    exact_config, exact_warnings = fetch_media_public_config(database_url, "video", provider, model)
    if exact_config.get("provider") == provider and exact_config.get("model") == model:
        return exact_config, exact_warnings
    # Model aliases can share one provider credential row (for example the
    # two xAI Grok models). Reuse that provider's public connection config,
    # then talking_head_video_config overlays the explicitly selected model.
    provider_config, provider_warnings = fetch_media_public_config(database_url, "video", provider)
    return provider_config, provider_warnings


def normalize_rel_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def copy_portrait_to_context(workspace: Path, portrait_path: str) -> str:
    rel_path = normalize_rel_path(portrait_path)
    if not rel_path:
        return ""
    source = workspace / rel_path
    if not source.exists():
        return rel_path
    target = workspace / "SessionContext" / "TalkingHead_Portrait" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
        shutil.copy2(source, target)
    return normalize_rel_path(str(target.relative_to(workspace)))


def srt_duration_warnings(items: list[dict[str, Any]], target_seconds: float) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if target_seconds <= 0:
        return warnings
    for item in items:
        try:
            start = float(item.get("start") or 0)
            end = float(item.get("end") or 0)
        except (TypeError, ValueError):
            continue
        duration = max(0.0, end - start)
        if duration > target_seconds:
            warnings.append(
                {
                    "type": "srt_duration_over_target",
                    "srt_id": item.get("srt_id") or item.get("id") or "",
                    "index": item.get("index"),
                    "duration_seconds": duration,
                    "target_seconds": target_seconds,
                    "message": "SRT duration exceeds target; TalkingHead_V1 keeps the original line and only warns.",
                }
            )
    return warnings


def run(workspace: Path, force: bool = False) -> dict[str, Any]:
    workspace = workspace.resolve()
    variables_path = workspace / "SessionContext" / "Variables.json"

    database_url = text_value(os.environ.get(DATABASE_URL_ENV)) or DEFAULT_DATABASE_URL
    task, config = fetch_talking_head_task_config(database_url, workspace)
    script_mode = text_value(task.get("script_creation_mode"))
    if script_mode not in {"user_provided", "ai_create", "ai_rewrite"}:
        raise RuntimeError(f"Unsupported TalkingHead_V1 script_creation_mode: {script_mode!r}")
    script_input = dict_value(config.get("script_input"))
    user_script = text_value(script_input.get("user_script_text"))
    reference_script = text_value(script_input.get("reference_script_text"))
    if script_mode == "user_provided" and not user_script:
        raise RuntimeError("user_provided mode requires user_script_text.")
    if script_mode == "ai_rewrite" and not reference_script:
        raise RuntimeError("ai_rewrite mode requires reference_script_text.")
    user_input = write_text_input(workspace, USER_SCRIPT_REL, user_script if script_mode == "user_provided" else "")
    reference_input = write_text_input(workspace, REFERENCE_SCRIPT_REL, reference_script if script_mode == "ai_rewrite" else "")
    raw_quick_config = dict_value(dict_value(config.get("storyboard")).get("quick_config"))
    quick_config_talking_head = dict_value(raw_quick_config.get("talking_head"))
    talking_head = dict_value(config.get("talking_head") or quick_config_talking_head)
    portrait = dict_value(talking_head.get("portrait"))
    voice_timing = dict_value(talking_head.get("voice_timing"))
    voice_provider = str(voice_timing.get("provider") or voice_timing.get("voice_provider") or DEFAULT_VOICE_CLONE_PROVIDER).strip().lower()
    segment_planning = dict_value(talking_head.get("segment_planning"))

    srt_payload = read_json(workspace / FINAL_SRT_ITEMS_REL, {"items": []}) or {"items": []}
    srt_items = srt_payload.get("items") if isinstance(srt_payload.get("items"), list) else []
    target_seconds = float(segment_planning.get("srt_target_seconds") or raw_quick_config.get("srt_target_seconds") or 8)
    portrait_segments_per_image = int(
        segment_planning.get("portrait_segments_per_image")
        or portrait.get("portrait_segments_per_image")
        or raw_quick_config.get("portrait_segments_per_image")
        or 2
    )
    source_portrait_path = normalize_rel_path(str(portrait.get("portrait_image_path") or ""))
    context_portrait_path = copy_portrait_to_context(workspace, source_portrait_path)
    warnings = srt_duration_warnings(srt_items, target_seconds)
    default_tts_config, tts_warnings = fetch_tts_public_config(database_url)
    default_image_config, image_warnings = fetch_media_public_config(database_url, "image")
    selected_video_model = dict_value(talking_head.get("video_model"))
    db_video_config, video_warnings = fetch_selected_video_public_config(database_url, selected_video_model)
    default_lipsync_config, lipsync_warnings = fetch_media_public_config(database_url, "lipsync")
    default_voice_clone_config, voice_clone_warnings = fetch_media_public_config(database_url, "voice-clone", voice_provider)
    warnings.extend(tts_warnings + image_warnings + video_warnings + lipsync_warnings + voice_clone_warnings)

    canonical_segment_planning = {
        **segment_planning,
        "shot_policy": segment_planning.get("shot_policy") or "single_shot",
        "scene_policy": segment_planning.get("scene_policy") or "single_scene",
        "segment_policy": "merge_srt_to_single_video_length",
        "srt_target_seconds": target_seconds,
        "portrait_segments_per_image": portrait_segments_per_image,
        "allow_sentence_split": False,
    }
    canonical_voice_timing = {
        **voice_timing,
        "provider": voice_provider,
        "voice_id": str(voice_timing.get("voice_id") or ""),
        "voice_label": str(voice_timing.get("voice_label") or ""),
        "tempo": float(voice_timing.get("tempo") or 1),
        "source": "SessionContext/Variables.json",
    }
    canonical_portrait = {
        **portrait,
        "portrait_image_path": source_portrait_path,
        "context_portrait_image_path": context_portrait_path or source_portrait_path,
        "portrait_segments_per_image": portrait_segments_per_image,
    }
    canonical_talking_head = {
        **{key: value for key, value in talking_head.items() if key != "video_model"},
        "portrait": canonical_portrait,
        "voice_timing": canonical_voice_timing,
        "voice_clone_config": {
            **default_voice_clone_config,
            "provider": voice_provider,
            "selected_voice_id": canonical_voice_timing["voice_id"],
            "selected_voice_label": canonical_voice_timing["voice_label"],
            "tempo": canonical_voice_timing["tempo"],
            "source": f"{default_voice_clone_config.get('source') or 'database'} + talking_head_voice_selection",
        },
        "segment_planning": canonical_segment_planning,
        "resource_strategy": {
            **dict_value(talking_head.get("resource_strategy")),
            "kind": "talking_head_only",
            "allow_cutaway": False,
        },
    }
    storyboard_quick_config = {
        **raw_quick_config,
        "enabled": raw_quick_config.get("enabled") is not False,
        "target_scene_seconds": float(raw_quick_config.get("target_scene_seconds") or target_seconds),
        "target_shot_seconds": target_seconds,
        "split_tolerance_seconds": float(raw_quick_config.get("split_tolerance_seconds") if raw_quick_config.get("split_tolerance_seconds") is not None else 0),
        "language_boundary_mode": str(raw_quick_config.get("language_boundary_mode") or "strict"),
        "shot_policy": "single_shot",
        "scene_policy": "single_scene",
        "segment_policy": "merge_srt_to_single_video_length",
        "srt_target_seconds": target_seconds,
        "workflow_profile": {
            **dict_value(raw_quick_config.get("workflow_profile")),
            "profile_id": WORKFLOW_ID,
            "workflow_id": WORKFLOW_ID,
            "create_mode": "person_talking_head",
        },
        "talking_head": canonical_talking_head,
    }
    business_context = dict_value(config.get("business_context"))
    script_prompt = dict_value(config.get("script_prompt"))
    storyboard_config = dict_value(config.get("storyboard"))
    run_model = dict_value(config.get("run_model"))
    script_input_snapshot = {
        "kind": {"user_provided": "user_script", "ai_create": "none", "ai_rewrite": "reference_script"}[script_mode],
        "user_script": user_input,
        "reference_script": reference_input,
        "script_format": text_value(script_input.get("script_format")) or "plain",
    }
    variables = {
        "schema_version": "talking_head_session_variables_1.0",
        "task": {
            "task_id": int(task.get("task_id") or 0),
            "session_id": int(task.get("session_id") or 0),
            "workflow_mode": WORKFLOW_ID,
            "title": text_value(task.get("title")),
            "config_schema_version": text_value(task.get("schema_version")),
            "config_revision": int(task.get("config_updated_at") or 0),
            "opencode_session_id": text_value(task.get("opencode_session_id")),
        },
        "workflow": {
            "script_creation": {
                "mode": script_mode,
                "input": script_input_snapshot,
                "simple_prompt": text_value(script_prompt.get("simple_prompt")),
                "final_prompt": text_value(script_prompt.get("final_prompt")),
                "prompt_model": {
                    "provider": text_value(script_prompt.get("model_provider")),
                    "model": text_value(script_prompt.get("model_id")),
                },
            },
            "business_context": business_context,
            "storyboard": {
                "simple_prompt": text_value(storyboard_config.get("simple_prompt")),
                "final_prompt": text_value(storyboard_config.get("final_prompt")),
                "quick_config": storyboard_quick_config,
            },
            "talking_head": canonical_talking_head,
        },
        "runtime": {
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "opencode_session_id": text_value(task.get("opencode_session_id")),
            "run_model_provider": text_value(run_model.get("provider")),
            "run_model_id": text_value(run_model.get("model_id")),
        },
        "workflow_id": WORKFLOW_ID,
        "profile_id": WORKFLOW_ID,
        "create_mode": "person_talking_head",
        "task_id": int(task.get("task_id") or 0),
        "session_id": int(task.get("session_id") or 0),
        "opencode_session_id": text_value(task.get("opencode_session_id")),
        "source_type": "script",
        "storyboard_quick_config": storyboard_quick_config,
        "talking_head": canonical_talking_head,
        "script_creation_mode": script_mode,
        "script_creation": {
            "mode": script_mode,
            "input": script_input_snapshot,
            "simple_prompt": text_value(script_prompt.get("simple_prompt")),
            "final_prompt": text_value(script_prompt.get("final_prompt")),
        },
        "business_context": business_context,
        "rewrite_prompt": {
            "simple_prompt": text_value(script_prompt.get("simple_prompt")),
            "final_prompt": text_value(script_prompt.get("final_prompt")),
            "source": "Variables.workflow.script_creation",
        },
        "storyboard_prompt": {
            "simple_prompt": text_value(storyboard_config.get("simple_prompt")),
            "final_prompt": text_value(storyboard_config.get("final_prompt")),
            "source": "Variables.workflow.storyboard",
        },
        "portrait_image_path": source_portrait_path,
        "talking_head_portrait_image_path": context_portrait_path or source_portrait_path,
        "voice_provider": voice_provider,
        "voice_id": canonical_voice_timing["voice_id"],
        "voice_label": canonical_voice_timing["voice_label"],
        "tempo": canonical_voice_timing["tempo"],
        "srt_target_seconds": target_seconds,
        "portrait_segments_per_image": portrait_segments_per_image,
        "resource_strategy": "talking_head_only",
        "run_model_provider": text_value(run_model.get("provider")),
        "run_model_id": text_value(run_model.get("model_id")),
        "default_tts_config": default_tts_config,
        "default_image_config": default_image_config,
        "default_video_config": talking_head_video_config(db_video_config, {"video_model": selected_video_model}),
        "default_lipsync_config": default_lipsync_config,
        "default_voice_clone_config": {
            **default_voice_clone_config,
            "provider": voice_provider,
            "selected_voice_id": canonical_voice_timing["voice_id"],
            "selected_voice_label": canonical_voice_timing["voice_label"],
            "tempo": canonical_voice_timing["tempo"],
            "source": f"{default_voice_clone_config.get('source') or 'database'} + talking_head_voice_selection",
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(variables_path, variables)

    report = {
        "schema_version": "talking_head_v1_prepare_session_variables_1.0",
        "workflow_id": WORKFLOW_ID,
        "status": "completed",
        "force": bool(force),
        "outputs": {
            "variables_path": normalize_rel_path(str(variables_path.relative_to(workspace))),
            "portrait_context_path": context_portrait_path,
            "srt_item_count": len(srt_items),
            "script_creation_mode": script_mode,
        },
        "warnings": warnings,
    }
    report_path = workspace / REPORT_DIR / "Result.json"
    write_json(report_path, report)
    return {
        **report,
        "outputs": {
            **report["outputs"],
            "result_path": normalize_rel_path(str(report_path.relative_to(workspace))),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare TalkingHead_V1 session variables.")
    parser.add_argument("--workspace", required=True, help="Task workspace path.")
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
