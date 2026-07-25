from __future__ import annotations

import base64
import http.client
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import TOOL_DIR_NAME, TOOL_NAME, TOOL_VERSION
from .io_utils import now_iso, read_json, relpath, write_json
from .paths import (
    INTERACTIVE_RANKING_REL,
    INTERACTIVE_STATE_REL,
    OUTPUT_FINAL_REL,
    OUTPUT_REFERENCE_PROFILE_REL,
    OUTPUT_SAMPLING_AUDIT_REL,
    OUTPUT_STAGE1_REL,
    OUTPUT_STAGE2_REL,
    PROMPT_DIR_REL,
    REPORT_RESULT_REL,
    SESSION_AUDIO_REFERENCE_REL,
    SESSION_CLOUD_CLONES_REL,
    SESSION_FINAL_ITEMS_REL,
    SESSION_TTS_DIR_REL,
    SESSION_TTS_FINAL_REL,
    WORKING_FINAL_ITEMS_REL,
    WORKING_FIT_DIR_REL,
    WORKING_RAW_DIR_REL,
    WORKING_REFERENCE_REL,
    WORKING_STATE_REL,
    WORKING_VARIABLES_REL,
    VARIABLES_REL,
    ensure_dirs,
)
from .quick02_bridge import load_quick02
from .scoring import (
    SCORING_DEGRADED,
    SCORING_FULL,
    SCORE_SCHEMA_VERSION,
    absolute_quality_score,
    build_age_proxy_score,
    build_articulation_score,
    build_candidate_explanation,
    build_final_score,
    build_penalties,
    build_persona_score,
    build_pitch_band_score,
    build_stage1_score,
    build_stage2_score,
    build_texture_score,
    build_timbre_rank_component,
    build_timbre_score,
    normalize_cosine,
    quality_penalty_score,
    ratio_score,
    rounded_scores,
)

try:
    from ToolLibrary.Analysis_V1.provider_audit import record_model_call_audit
except Exception:  # pragma: no cover - package path differs in some runners
    try:
        from OpenCrew.ToolLibrary.Analysis_V1.provider_audit import record_model_call_audit
    except Exception:  # pragma: no cover
        record_model_call_audit = None  # type: ignore[assignment]


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def record_provider_tts_audit(
    quick02: Any,
    *,
    workspace: Path | None,
    asset_key: str,
    provider: str,
    model: str,
    voice: str,
    prompt_path: Path,
    output_path: Path,
    text_value: str,
    response: dict[str, Any],
    status: str = "ok",
    error_code: str = "",
) -> None:
    if workspace is None or record_model_call_audit is None:
        return
    try:
        duration = float(response.get("duration") or quick02.media_duration(output_path) or 0.0)
        non_space_chars = len("".join(str(text_value or "").split()))
        record_model_call_audit(
            workspace=workspace,
            tool_dir_name=TOOL_DIR_NAME,
            tool_name=TOOL_NAME,
            step_index=5,
            asset_key=asset_key or output_path.stem,
            kind="TTS",
            provider=provider,
            model_id=model,
            request={
                "provider": provider,
                "model": model,
                "voice": voice,
                "prompt_path": relpath(prompt_path, workspace),
            },
            response={
                **response,
                "provider": provider,
                "model": model,
                "voice": voice,
                "usage": {
                    "request": 1,
                    "character": non_space_chars,
                    "prompt_character": len("".join(prompt_path.read_text(encoding="utf-8").split())),
                    "audio_second_observed": round(duration, 3),
                },
            },
            status=status,
            error_code=error_code,
            prompt_path=relpath(prompt_path, workspace),
            output_summary=f"{provider} TTS {status}",
        )
    except Exception:
        return


@dataclass(frozen=True)
class AdvArgs:
    workspace: str
    voice_catalog_dir: str
    providers: str
    model: str
    voices: str
    reference_start: float
    reference_duration: float
    stage1_count: int
    stage2_count: int
    final_count: int
    database_url: str
    database_url_env: str
    disable_speechbrain: bool
    force: bool
    resume: bool
    print_json: bool


def base_result(workspace: Path, args: AdvArgs) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": True,
        "requires_model_calls": True,
        "inputs": {},
        "outputs": {},
        "counts": {},
        "warnings": [],
        "blocked_reasons": [],
        "force": bool(args.force),
        "resume": bool(args.resume),
        "updated_at": now_iso(),
    }


def validate_workspace(workspace: Path) -> None:
    for rel in (VARIABLES_REL, SESSION_FINAL_ITEMS_REL, SESSION_AUDIO_REFERENCE_REL):
        if not (workspace / rel).exists():
            raise BlockedError("required_input_missing", f"Required input is missing: {rel}")


def load_inputs(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    variables = read_json(workspace / VARIABLES_REL)
    final_items = read_json(workspace / SESSION_FINAL_ITEMS_REL)
    if not isinstance(variables, dict):
        variables = {}
    if not isinstance(final_items, dict) or not isinstance(final_items.get("items"), list) or not final_items.get("items"):
        raise BlockedError("final_srt_frame_items_empty", f"{SESSION_FINAL_ITEMS_REL} has no items.")
    write_json(workspace / WORKING_VARIABLES_REL, variables)
    write_json(workspace / WORKING_FINAL_ITEMS_REL, final_items)
    return variables, final_items


def quick02_args(quick02: Any, workspace: Path, args: AdvArgs) -> Any:
    return quick02.Args(
        workspace=str(workspace),
        voice_catalog_dir=args.voice_catalog_dir,
        provider=primary_provider(args),
        model=args.model,
        voices=args.voices,
        reference_start=float(args.reference_start),
        reference_duration=float(args.reference_duration),
        final_count=int(args.final_count),
        database_url=args.database_url,
        database_url_env=args.database_url_env,
        force=bool(args.force),
        resume=bool(args.resume),
        print_json=bool(args.print_json),
    )


def provider_tokens(args: AdvArgs) -> list[str]:
    values = [item.strip().lower() for item in str(args.providers or "").replace(";", ",").split(",") if item.strip()]
    if not values:
        model = str(args.model or "").lower()
        if "qwen" in model:
            values = ["qwen"]
        elif "seed-tts" in model or "bytedance" in model or "doubao" in model:
            values = ["bytedance"]
        else:
            values = ["google"]
    return ["google" if item == "gemini" else item for item in values]


def primary_provider(args: AdvArgs) -> str:
    return provider_tokens(args)[0]


def provider_allowed(args: AdvArgs, provider: str) -> bool:
    normalized = "google" if str(provider or "").strip().lower() == "gemini" else str(provider or "").strip().lower()
    allowed = provider_tokens(args)
    return "all" in allowed or normalized in allowed


def load_voice_catalog(quick02: Any, cdir: Path, args: AdvArgs) -> dict[str, Any]:
    if not cdir.exists() or not cdir.is_dir():
        raise BlockedError("voice_catalog_missing", f"Voice catalog directory does not exist: {cdir}")
    index_path = cdir / "voice_catalog_index.json"
    if not index_path.exists():
        raise BlockedError("voice_catalog_index_missing", f"Voice catalog index is missing: {index_path}")
    payload = read_json(index_path)
    if not isinstance(payload, dict):
        raise BlockedError("voice_catalog_index_invalid", f"Voice catalog index must be a JSON object: {index_path}")
    provider = "google" if str(payload.get("provider") or "").strip().lower() == "gemini" else str(payload.get("provider") or "").strip().lower()
    if provider not in {"google", "qwen", "bytedance"}:
        raise BlockedError("unsupported_catalog_provider", f"03_03 supports Google/Gemini, Qwen, and ByteDance catalogs, got provider={provider}.")
    if not provider_allowed(args, provider):
        raise BlockedError("unsupported_provider", f"03_03 provider allowlist {args.providers!r} does not include catalog provider={provider}.")
    model = str(payload.get("model") or "").strip()
    if model and str(args.model or model).strip() != model:
        raise BlockedError("voice_catalog_model_mismatch", f"Catalog model={model} does not match requested model={args.model or model}.")
    if str(payload.get("sample_text_id") or "") != getattr(quick02, "CATALOG_SAMPLE_TEXT_ID", "fixed_cn_v1"):
        raise BlockedError("voice_catalog_sample_text_mismatch", "Voice catalog sample_text_id must be fixed_cn_v1.")
    voices = payload.get("voices")
    if not isinstance(voices, list) or not voices:
        raise BlockedError("voice_catalog_empty", "Voice catalog has no voices.")
    requested = quick02.requested_catalog_voices(quick02_args(quick02, cdir, args))
    filtered = []
    for item in voices:
        if not isinstance(item, dict):
            continue
        voice = quick02.catalog_item_voice(item)
        if requested and voice not in requested:
            continue
        audio_path = quick02.catalog_item_audio_path(cdir, item)
        if not voice or not audio_path.exists() or not audio_path.is_file():
            raise BlockedError(
                "voice_catalog_audio_missing",
                f"Required system voice catalog audio is missing for voice={voice}: {audio_path}. "
                "Generate and commit Analysis_V1 VoiceCatalog wav assets before enabling this provider for matching.",
            )
        filtered.append(item)
    if len(filtered) < max(1, int(args.final_count)):
        raise BlockedError("voice_catalog_too_small", f"Voice catalog has only {len(filtered)} usable voices; need {args.final_count}.")
    payload["voices"] = filtered
    return payload


def load_provider_api_key(quick02: Any, workspace: Path, args: AdvArgs, provider: str, model: str) -> str:
    provider_id = "google" if str(provider or "").strip().lower() == "gemini" else str(provider or "").strip().lower()
    if provider_id == "google":
        return quick02.load_tts_api_key(quick02_args(quick02, workspace, args), provider_id, model)
    if provider_id == "qwen":
        env_provider = os.environ.get("OPENCREW_TTS_PROVIDER", "").strip().lower()
        env_key = os.environ.get("OPENCREW_TTS_API_KEY", "").strip()
        if env_key and env_provider in {"qwen", "dashscope"}:
            return env_key
        for env_name in ("DASHSCOPE_API_KEY", "QWEN_API_KEY"):
            value = os.environ.get(env_name, "").strip()
            if value:
                return value
    if provider_id == "bytedance":
        env_provider = os.environ.get("OPENCREW_TTS_PROVIDER", "").strip().lower()
        env_key = os.environ.get("OPENCREW_TTS_API_KEY", "").strip()
        if env_key and env_provider in {"bytedance", "volcengine", "byteplus", "doubao"}:
            return env_key
        for env_name in ("BYTEDANCE_TTS_API_KEY", "VOLCENGINE_TTS_API_KEY", "BYTEPLUS_TTS_API_KEY"):
            value = os.environ.get(env_name, "").strip()
            if value:
                return value
    helper = quick02.load_tts_builder_g_module()
    if helper is None:
        raise BlockedError("tts_api_key_loader_unavailable", "Cannot load shared TTS API-key helper.")
    qargs = quick02_args(quick02, workspace, args)
    conn = helper.postgres_connect(helper.resolve_database_url(qargs))
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
SELECT api_key_ref, api_key_ciphertext
FROM {helper.CONFIG_TABLE}
WHERE kind = %s AND provider = %s AND enabled = TRUE
ORDER BY (model = %s) DESC, active DESC, id ASC
LIMIT 1
""",
                ("tts", provider_id, model),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return ""
    api_key_ref = helper.decode_db_value(row[0]).strip()
    legacy_key = helper.decode_db_value(row[1] if len(row) > 1 else "").strip()
    return helper.resolve_secret_value(api_key_ref, legacy_key)


def load_provider_extra_json(quick02: Any, workspace: Path, args: AdvArgs, provider: str, model: str) -> dict[str, Any]:
    provider_id = "google" if str(provider or "").strip().lower() == "gemini" else str(provider or "").strip().lower()
    helper = quick02.load_tts_builder_g_module()
    if helper is None:
        return {}
    qargs = quick02_args(quick02, workspace, args)
    conn = helper.postgres_connect(helper.resolve_database_url(qargs))
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
SELECT extra_json
FROM {helper.CONFIG_TABLE}
WHERE kind = %s AND provider = %s AND enabled = TRUE
ORDER BY (model = %s) DESC, active DESC, id ASC
LIMIT 1
""",
                ("tts", provider_id, model),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    try:
        payload = json.loads(str(row[0] or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_provider_tts_config(quick02: Any, workspace: Path, args: AdvArgs, provider: str, model: str) -> dict[str, Any]:
    return {
        "api_key": load_provider_api_key(quick02, workspace, args, provider, model),
        "extra": load_provider_extra_json(quick02, workspace, args, provider, model),
    }


def post_qwen_json(api_key: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise RuntimeError(f"DashScope HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DashScope network error: {exc}") from exc


def qwen_audio_url_or_data(response: dict[str, Any]) -> tuple[str, str]:
    output = response.get("output") if isinstance(response.get("output"), dict) else {}
    audio = output.get("audio") if isinstance(output.get("audio"), dict) else {}
    audio_url = str(audio.get("url") or "").strip()
    audio_data = str(audio.get("data") or "").strip()
    if audio_url:
        return audio_url, "url"
    if audio_data:
        return audio_data, "base64"
    raise RuntimeError(f"Qwen TTS response did not include audio url/data: {json.dumps(response, ensure_ascii=False)[:1000]}")


def write_qwen_audio(value: str, kind: str, output_path: Path, *, attempts: int = 3) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "base64":
        output_path.write_bytes(base64.b64decode(value))
        return {"audio_source": "base64", "bytes": output_path.stat().st_size}
    safe_attempts = max(1, int(attempts or 1))
    last_error: Exception | None = None
    data = b""
    mime = ""
    for attempt in range(1, safe_attempts + 1):
        request = urllib.request.Request(value, headers={"User-Agent": "OpenCrew/AnalysisV1-Qwen-TTS"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=120) as response:
                data = response.read()
                mime = response.headers.get("Content-Type", "")
            if not data:
                raise RuntimeError("Qwen TTS returned an empty audio download.")
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500 or attempt >= safe_attempts:
                raise RuntimeError(f"Qwen TTS audio download failed with HTTP {exc.code}.") from exc
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, ConnectionError, OSError, RuntimeError) as exc:
            last_error = exc
            if attempt >= safe_attempts:
                raise RuntimeError(f"Qwen TTS audio download failed after {safe_attempts} attempts: {exc}") from exc
        time.sleep(min(2.0, 0.35 * attempt))
    if not data:
        raise RuntimeError(f"Qwen TTS returned an empty audio download after {safe_attempts} attempts: {last_error}")
    output_path.write_bytes(data)
    return {"audio_source": "url", "mime_type": mime, "bytes": len(data), "download_attempts": attempt}


def call_qwen_tts(quick02: Any, api_key: str, model: str, voice: str, prompt_path: Path, output_path: Path, *, workspace: Path | None = None, asset_key: str = "") -> dict[str, Any]:
    prompt_text = prompt_path.read_text(encoding="utf-8")
    text_value = quick02.extract_tts_body(prompt_text)
    input_payload: dict[str, Any] = {
        "text": text_value,
        "voice": voice,
        "language_type": "Chinese",
    }
    if "instruct" in model:
        instruction = prompt_text.replace(text_value, "").strip()[:1600] or "自然中文短视频旁白，吐字清晰，节奏贴合画面。"
        input_payload["instructions"] = instruction
        input_payload["optimize_instructions"] = True
    response = post_qwen_json(api_key, {"model": model, "input": input_payload})
    audio_value, audio_kind = qwen_audio_url_or_data(response)
    audio_meta = write_qwen_audio(audio_value, audio_kind, output_path)
    meta = {**audio_meta, "provider": "qwen", "model": model, "voice": voice, "duration": quick02.media_duration(output_path), "prompt_path": str(prompt_path)}
    record_provider_tts_audit(quick02, workspace=workspace, asset_key=asset_key, provider="qwen", model=model, voice=voice, prompt_path=prompt_path, output_path=output_path, text_value=text_value, response=meta)
    return meta


def load_media_model_config_module() -> Any:
    repo_root = Path(__file__).resolve().parents[3]
    for path in (repo_root / "backend", repo_root / "ModelConfig" / "backend"):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)
    from opcrew_model_config import media_model_config

    return media_model_config


def call_bytedance_tts(quick02: Any, api_key: str, model: str, voice: str, prompt_path: Path, output_path: Path, provider_extra: dict[str, Any] | None = None, *, workspace: Path | None = None, asset_key: str = "") -> dict[str, Any]:
    prompt_text = prompt_path.read_text(encoding="utf-8")
    text_value = quick02.extract_tts_body(prompt_text)
    media_model_config = load_media_model_config_module()
    audio_data, mime_type = media_model_config.bytedance_tts_audio_bytes(
        api_key,
        model,
        voice,
        text_value,
        provider_extra or {},
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(audio_data)
    meta = {
        "audio_source": "bytes",
        "mime_type": mime_type,
        "bytes": len(audio_data),
        "provider": "bytedance",
        "model": model,
        "voice": voice,
        "duration": quick02.media_duration(output_path),
        "prompt_path": str(prompt_path),
    }
    record_provider_tts_audit(quick02, workspace=workspace, asset_key=asset_key, provider="bytedance", model=model, voice=voice, prompt_path=prompt_path, output_path=output_path, text_value=text_value, response=meta)
    return meta


def call_provider_tts(quick02: Any, api_key: str, provider: str, model: str, voice: str, prompt_path: Path, output_path: Path, *, workspace: Path | None = None, asset_key: str = "", provider_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    provider_id = "google" if str(provider or "").strip().lower() == "gemini" else str(provider or "").strip().lower()
    if provider_id == "google":
        meta = quick02.call_gemini_tts(api_key, model, voice, prompt_path, output_path, workspace=workspace, asset_key=asset_key)
        return {**meta, "provider": "google", "model": model, "voice": voice}
    if provider_id == "qwen":
        return call_qwen_tts(quick02, api_key, model, voice, prompt_path, output_path, workspace=workspace, asset_key=asset_key)
    if provider_id == "bytedance":
        return call_bytedance_tts(quick02, api_key, model, voice, prompt_path, output_path, provider_extra, workspace=workspace, asset_key=asset_key)
    raise BlockedError("unsupported_tts_provider", f"03_03 candidate generation does not support provider={provider_id}.")


def session_candidate_path(rank: int) -> str:
    return f"{SESSION_TTS_DIR_REL}/tts_builder_candidate_{rank:03d}.wav"


def quick01_args(helper: Any, workspace: Path, args: AdvArgs) -> Any:
    return helper.Args(
        workspace=str(workspace),
        mode="normal",
        scene_profile_mode="auto",
        tts_model=args.model,
        scene_model="",
        voices=args.voices,
        target_duration=16.0,
        quick_duration=8.0,
        reference_start=float(args.reference_start),
        reference_duration=float(args.reference_duration),
        top_voices=3,
        final_count=int(args.final_count),
        max_scene_frames=8,
        database_url=args.database_url,
        database_url_env=args.database_url_env,
        force=bool(args.force),
        resume=bool(args.resume),
        force_regenerate_prompts=False,
        print_json=bool(args.print_json),
    )


def build_prompt_planner_request(
    *,
    provider: str,
    model: str,
    voice: str,
    variant: str,
    scene_profile: dict[str, Any],
    reference_profile: dict[str, Any],
    reference_text: str,
    row: dict[str, Any],
    fallback_prompt: str,
) -> str:
    planner_input = {
        "provider": provider,
        "model": model,
        "voice": voice,
        "variant": variant,
        "reference_text": reference_text,
        "scene_profile": scene_profile,
        "reference_voice_profile": {
            "selected_duration": reference_profile.get("selected_duration"),
            "features": reference_profile.get("features"),
            "gender_gate": reference_profile.get("gender_gate"),
            "dimension_profile": reference_profile.get("dimension_profile"),
        },
        "candidate_scores": {
            "match_score": row.get("match_score") or row.get("score"),
            "stage2_score": row.get("stage2_score"),
            "dimension_scores": row.get("dimension_scores"),
            "raw_scores": row.get("raw_scores"),
            "tempo_prior": row.get("tempo"),
            "voice_label": row.get("voice_label"),
        },
        "fallback_prompt": fallback_prompt,
    }
    return (
        "你是 TTS 声音提示词规划器。请根据参考声音分析、候选音色评分和目标正文，生成一个可直接交给 TTS 的中文提示词。\n"
        "要求：只返回 JSON，不要 Markdown。JSON 字段必须包含：prompt、style_notes、avoid、pace_instruction、reason。\n"
        "prompt 必须包含“只朗读正文”的明确要求，并保留独立的“正文：\\n<原始正文>”段落；不要加入旁白解释；不要改变正文语义。\n"
        "如果 provider/model 不支持复杂指令，也要生成一个短而明确的 prompt，供审计和支持指令的模型使用。\n\n"
        f"输入：\n{json.dumps(planner_input, ensure_ascii=False, indent=2)}"
    )


def call_prompt_planner(
    quick02: Any,
    workspace: Path,
    args: AdvArgs,
    variables: dict[str, Any],
    planner_prompt_path: Path,
) -> dict[str, Any] | None:
    helper = quick02.load_tts_builder_g_module()
    if helper is None:
        return None
    session_id = str(variables.get("opencode_session_id") or "").strip()
    provider = str(variables.get("run_model_provider") or "").strip()
    model = str(variables.get("run_model_id") or "").strip()
    if not session_id or not provider or not model:
        return None
    runtime = helper.fetch_opencode_runtime(quick01_args(helper, workspace, args))
    directory = str(variables.get("workspace_dir") or workspace)
    payload = {
        "parts": [{"type": "text", "text": planner_prompt_path.read_text(encoding="utf-8")}],
        "model": {"providerID": provider, "modelID": model},
    }
    started_at = helper.now_ms()
    helper.opencode_request(runtime, "POST", f"/session/{urllib.parse.quote(session_id, safe='')}/prompt_async", payload, directory, timeout=30)
    deadline = time.time() + 240
    while time.time() < deadline:
        messages = helper.opencode_request(runtime, "GET", f"/session/{urllib.parse.quote(session_id, safe='')}/message", None, directory, timeout=30) or []
        assistant_text = helper.last_completed_assistant(messages, started_at)
        if assistant_text:
            parsed = helper.parse_json_from_text(assistant_text)
            return parsed if isinstance(parsed, dict) else None
        time.sleep(1)
    return None


def plan_candidate_prompt(
    quick02: Any,
    workspace: Path,
    args: AdvArgs,
    variables: dict[str, Any],
    provider: str,
    model: str,
    voice: str,
    variant: str,
    scene_profile: dict[str, Any],
    reference_profile: dict[str, Any],
    reference_text: str,
    row: dict[str, Any],
    planner_rel: str,
) -> tuple[str, dict[str, Any]]:
    fallback_prompt = quick02.build_model_prompt(scene_profile, voice, reference_text, float(row.get("tempo") or 1.0), variant)
    planner_prompt = build_prompt_planner_request(
        provider=provider,
        model=model,
        voice=voice,
        variant=variant,
        scene_profile=scene_profile,
        reference_profile=reference_profile,
        reference_text=reference_text,
        row=row,
        fallback_prompt=fallback_prompt,
    )
    planner_path = workspace / planner_rel
    planner_path.parent.mkdir(parents=True, exist_ok=True)
    planner_path.write_text(planner_prompt, encoding="utf-8")
    try:
        planned = call_prompt_planner(quick02, workspace, args, variables, planner_path)
    except Exception as exc:
        return fallback_prompt, {
            "prompt_source": "rule_fallback",
            "planner_prompt_path": planner_rel,
            "planner_error": str(exc)[:500],
            "prompt_model_call_made": False,
        }
    prompt = normalize_planned_prompt(str((planned or {}).get("prompt") or "").strip(), reference_text)
    if not prompt:
        return fallback_prompt, {
            "prompt_source": "rule_fallback",
            "planner_prompt_path": planner_rel,
            "prompt_model_call_made": False,
        }
    response_rel = planner_rel.replace("_planner_prompt.md", "_planner_response.json")
    write_json(workspace / response_rel, planned or {})
    return prompt, {
        "prompt_source": "llm_prompt_planner",
        "planner_prompt_path": planner_rel,
        "planner_response_path": response_rel,
        "prompt_model_call_made": True,
        "planner_reason": str((planned or {}).get("reason") or "")[:500],
    }


def normalize_planned_prompt(prompt: str, reference_text: str) -> str:
    text = str(prompt or "").strip()
    body = str(reference_text or "").strip()
    if not text or not body or "正文：" in text or "正文:" in text:
        return text
    return f"{text}\n\n正文：\n{body}"


def snapshot_session_tts_outputs(workspace: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    final_path = workspace / SESSION_TTS_FINAL_REL
    if final_path.exists() and final_path.is_file():
        snapshot[SESSION_TTS_FINAL_REL] = final_path.read_bytes()
    tts_dir = workspace / SESSION_TTS_DIR_REL
    if tts_dir.exists():
        for path in tts_dir.glob("tts_builder_candidate_*.wav"):
            if path.is_file():
                snapshot[relpath(path, workspace)] = path.read_bytes()
    return snapshot


def restore_session_tts_outputs(workspace: Path, snapshot: dict[str, bytes], result: dict[str, Any]) -> None:
    tts_dir = workspace / SESSION_TTS_DIR_REL
    if tts_dir.exists():
        for path in tts_dir.glob("tts_builder_candidate_*.wav"):
            if path.is_file():
                path.unlink()
    final_path = workspace / SESSION_TTS_FINAL_REL
    if final_path.exists():
        final_path.unlink()
    for rel, data in snapshot.items():
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    result.setdefault("warnings", []).append({
        "code": "session_tts_outputs_restored",
        "message": "Previous SessionOutput TTS candidates were restored because the forced QuickAdv rerun did not complete.",
    })


def choose_reference_window(quick02: Any, items: list[dict[str, Any]], args: AdvArgs) -> dict[str, Any]:
    duration = float(args.reference_duration or 0.0)
    if duration > 0:
        return quick02.forced_sample_window(items, float(args.reference_start or 0.0), duration)
    return quick02.choose_sample_window(items, 16.0)


def extract_reference_audio(quick02: Any, workspace: Path, start: float, duration: float) -> Path:
    source = workspace / SESSION_AUDIO_REFERENCE_REL
    if not source.exists() or not source.is_file():
        raise BlockedError("reference_audio_missing", f"Required reference audio is missing: {SESSION_AUDIO_REFERENCE_REL}. Run 02_01_AudioASR.py first.")
    selected = workspace / WORKING_REFERENCE_REL
    selected.parent.mkdir(parents=True, exist_ok=True)
    safe_start = max(0.0, float(start or 0.0))
    safe_duration = max(0.1, float(duration or 16.0))
    quick02.run_cmd([
        quick02.find_binary("ffmpeg"),
        "-y",
        "-ss",
        f"{safe_start:.3f}",
        "-t",
        f"{safe_duration:.3f}",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(selected),
    ], timeout=120)
    if not selected.exists() or selected.stat().st_size <= 0:
        raise BlockedError("reference_audio_extract_failed", f"Could not extract selected reference audio range {safe_start:.3f}-{safe_start + safe_duration:.3f}.")
    return selected


def vad_sampling_metrics(path: Path) -> dict[str, Any]:
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r".*pkg_resources is deprecated as an API.*",
                category=UserWarning,
            )
            import webrtcvad  # type: ignore

        with wave.open(str(path), "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            rate = reader.getframerate()
            raw = reader.readframes(reader.getnframes())
        if channels != 1 or sample_width != 2 or rate not in {8000, 16000, 32000, 48000}:
            return {"vad_backend": "unsupported_pcm", "vad_voice_ratio": 0.0, "vad_segment_count": 0, "vad_score": 45.0}
        vad = webrtcvad.Vad(2)
        frame_ms = 30
        frame_bytes = int(rate * frame_ms / 1000) * sample_width
        flags = []
        for offset in range(0, max(0, len(raw) - frame_bytes + 1), frame_bytes):
            frame = raw[offset:offset + frame_bytes]
            if len(frame) == frame_bytes:
                flags.append(bool(vad.is_speech(frame, rate)))
        if not flags:
            return {"vad_backend": "webrtcvad", "vad_voice_ratio": 0.0, "vad_segment_count": 0, "vad_score": 35.0}
        voiced = sum(1 for flag in flags if flag)
        segments = 0
        previous = False
        for flag in flags:
            if flag and not previous:
                segments += 1
            previous = flag
        ratio = voiced / max(1, len(flags))
        ideal = 0.72
        score = max(0.0, 100.0 * (1.0 - min(1.0, abs(ratio - ideal) / ideal)))
        if segments <= 0:
            score *= 0.5
        return {
            "vad_backend": "webrtcvad",
            "vad_voice_ratio": round(float(ratio), 6),
            "vad_segment_count": int(segments),
            "vad_score": round(float(score), 3),
        }
    except Exception:
        return {"vad_backend": "energy_fallback", "vad_voice_ratio": 0.0, "vad_segment_count": 0, "vad_score": 50.0}


def boundary_sampling_metrics(quick02: Any, window: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in (window.get("items") or []) if isinstance(item, dict)]
    start = float(window.get("start") or 0.0)
    end = float(window.get("end") or start + float(window.get("duration") or 16.0))
    if not items:
        return {"boundary_score": 35.0, "complete_item_ratio": 0.0, "cut_edge_count": 2}
    tolerance = 0.08
    complete = [
        item for item in items
        if quick02.item_start(item) >= start - tolerance and quick02.item_end(item) <= end + tolerance
    ]
    cut_start = any(quick02.item_start(item) < start - tolerance < quick02.item_end(item) for item in items)
    cut_end = any(quick02.item_start(item) < end + tolerance < quick02.item_end(item) for item in items)
    cut_edges = int(cut_start) + int(cut_end)
    complete_ratio = len(complete) / max(1, len(items))
    score = max(0.0, min(100.0, 100.0 * complete_ratio - 18.0 * cut_edges + min(10.0, len(items) * 2.0)))
    return {
        "boundary_score": round(float(score), 3),
        "complete_item_ratio": round(float(complete_ratio), 6),
        "cut_edge_count": int(cut_edges),
    }


def text_sampling_metrics(quick02: Any, text: str, duration: float) -> dict[str, Any]:
    chars = quick02.count_cjk(text)
    unique_chars = len(set(ch for ch in text if "\u4e00" <= ch <= "\u9fff"))
    punctuation_count = sum(1 for ch in text if ch in "，。！？、；：,.!?;:")
    target_chars = max(28.0, float(duration or 16.0) * 4.8)
    text_coverage_score = max(0.0, min(100.0, 100.0 * chars / target_chars))
    lexical_diversity_score = max(30.0, min(100.0, 100.0 * unique_chars / max(1, chars)))
    punctuation_score = max(35.0, min(100.0, 50.0 + punctuation_count * 8.0))
    return {
        "dialogue_chars": int(chars),
        "unique_cjk_chars": int(unique_chars),
        "punctuation_count": int(punctuation_count),
        "text_coverage_score": round(float(text_coverage_score), 3),
        "lexical_diversity_score": round(float(lexical_diversity_score), 3),
        "punctuation_score": round(float(punctuation_score), 3),
    }


def reference_sampling_scores(quick02: Any, reference_audio: Path, window: dict[str, Any], reference_text: str, features: dict[str, Any]) -> dict[str, Any]:
    duration = float(window.get("duration") or features.get("duration") or 16.0)
    vad = vad_sampling_metrics(reference_audio)
    boundary = boundary_sampling_metrics(quick02, window)
    text = text_sampling_metrics(quick02, reference_text, duration)
    silence_ratio = safe_float(features.get("silence_ratio"), 0.0)
    voice_activity_score = max(0.0, min(100.0, (1.0 - min(1.0, silence_ratio)) * 100.0))
    if safe_float(vad.get("vad_score"), 0.0) > 0:
        voice_activity_score = 0.55 * voice_activity_score + 0.45 * safe_float(vad.get("vad_score"), 50.0)
    consonant_proxy = safe_float(features.get("consonant_proxy"), 50.0)
    sibilance_proxy = safe_float(features.get("sibilance_proxy"), 0.0) * 100.0
    consonant_integrity_score = max(0.0, min(100.0, 0.55 * safe_float(boundary.get("boundary_score"), 50.0) + 0.30 * consonant_proxy + 0.15 * sibilance_proxy))
    pitch_range_score = max(35.0, min(100.0, safe_float(features.get("pitch_range_hz"), 0.0) / 180.0 * 100.0))
    texture_score = max(35.0, min(100.0, 0.35 * safe_float(features.get("warmth_ratio"), 1.0) * 60.0 + 0.25 * safe_float(features.get("roughness_proxy"), 0.0) * 100.0 + 0.20 * safe_float(features.get("nasality_proxy"), 0.0) * 100.0 + 0.20 * sibilance_proxy))
    feature_diversity_score = max(35.0, min(100.0, 0.40 * pitch_range_score + 0.35 * texture_score + 0.25 * safe_float(text.get("lexical_diversity_score"), 50.0)))
    energy_stability_score = max(0.0, min(100.0, safe_float(features.get("energy_stability_score"), safe_float(features.get("energy"), 0.0) * 100.0)))
    sampling_score = (
        0.22 * voice_activity_score
        + 0.18 * safe_float(boundary.get("boundary_score"), 50.0)
        + 0.17 * safe_float(text.get("text_coverage_score"), 50.0)
        + 0.15 * consonant_integrity_score
        + 0.14 * energy_stability_score
        + 0.14 * feature_diversity_score
    )
    return {
        **vad,
        **boundary,
        **text,
        "voice_activity_score": round(float(voice_activity_score), 3),
        "consonant_integrity_score": round(float(consonant_integrity_score), 3),
        "energy_stability_score": round(float(energy_stability_score), 3),
        "feature_diversity_score": round(float(feature_diversity_score), 3),
        "pitch_range_score": round(float(pitch_range_score), 3),
        "texture_coverage_score": round(float(texture_score), 3),
        "sampling_score": round(float(sampling_score), 3),
    }


def sample_reference(workspace: Path, args: AdvArgs, result: dict[str, Any] | None = None) -> dict[str, Any]:
    quick02 = load_quick02()
    validate_workspace(workspace)
    _, final_items = load_inputs(workspace)
    items = [item for item in final_items.get("items", []) if isinstance(item, dict) and quick02.dialogue(item)]
    if not items:
        raise BlockedError("final_srt_dialogue_empty", "No dialogue items are available for reference sampling.")
    window = choose_reference_window(quick02, items, args)
    reference_audio = extract_reference_audio(quick02, workspace, float(window.get("start") or 0.0), float(window.get("duration") or args.reference_duration or 16.0))
    reference_text = quick02.selected_dialogue(window.get("items") or [])
    reference_rate = quick02.count_cjk(reference_text) / max(0.1, float(window.get("duration") or 16.0))
    features = advanced_audio_features(quick02, reference_audio, reference_rate, float(window.get("duration") or 16.0))
    sampling_metrics = reference_sampling_scores(quick02, reference_audio, window, reference_text, features)
    sampling_score = safe_float(sampling_metrics.get("sampling_score"), 0.0)
    target_gender = quick02.infer_target_gender(quick02.load_scene_profile(workspace, {}, reference_text), features)
    reference_profile = {
        "audio_path": relpath(reference_audio, workspace),
        "selected_range": {"start": window.get("start"), "end": window.get("end")},
        "selected_duration": window.get("duration"),
        "dialogue_chars": quick02.count_cjk(reference_text),
        "dialogue": reference_text,
        "features": quick02.rounded_feature_map(features),
        "gender_gate": target_gender,
        "sampling_metrics": sampling_metrics,
    }
    audit = {
        "selected_range": reference_profile["selected_range"],
        "selected_duration": reference_profile["selected_duration"],
        "sampling_score": round(float(sampling_score), 3),
        "score_parts": {
            "voice_activity_score": safe_float(sampling_metrics.get("voice_activity_score")),
            "boundary_score": safe_float(sampling_metrics.get("boundary_score")),
            "text_coverage_score": safe_float(sampling_metrics.get("text_coverage_score")),
            "consonant_integrity_score": safe_float(sampling_metrics.get("consonant_integrity_score")),
            "energy_stability_score": safe_float(sampling_metrics.get("energy_stability_score")),
            "feature_diversity_score": safe_float(sampling_metrics.get("feature_diversity_score")),
        },
        "vad": {key: sampling_metrics.get(key) for key in ("vad_backend", "vad_voice_ratio", "vad_segment_count", "vad_score")},
        "coverage": {
            "dialogue_chars": sampling_metrics.get("dialogue_chars"),
            "unique_cjk_chars": sampling_metrics.get("unique_cjk_chars"),
            "punctuation_count": sampling_metrics.get("punctuation_count"),
            "complete_item_ratio": sampling_metrics.get("complete_item_ratio"),
            "cut_edge_count": sampling_metrics.get("cut_edge_count"),
        },
        "quality_label": "good" if sampling_score >= 80 else "usable" if sampling_score >= 60 else "weak",
        "updated_at": now_iso(),
    }
    write_json(workspace / OUTPUT_REFERENCE_PROFILE_REL, reference_profile)
    write_json(workspace / OUTPUT_SAMPLING_AUDIT_REL, audit)
    if result is not None:
        result.setdefault("outputs", {})["reference_voice_profile"] = OUTPUT_REFERENCE_PROFILE_REL
        result.setdefault("outputs", {})["reference_sampling_audit"] = OUTPUT_SAMPLING_AUDIT_REL
    return {"reference_profile": reference_profile, "sampling_audit": audit}


def catalog_dir(workspace: Path, args: AdvArgs) -> Path:
    raw = str(args.voice_catalog_dir or "").strip()
    if not raw:
        return Path(__file__).resolve().parents[1] / "VoiceCatalog" / args.model
    path = Path(raw).expanduser()
    return path if path.is_absolute() else workspace / path


def catalog_list(workspace: Path, args: AdvArgs) -> dict[str, Any]:
    quick02 = load_quick02()
    cdir = catalog_dir(workspace, args)
    catalog = load_voice_catalog(quick02, cdir, args)
    voices = []
    for item in catalog.get("voices") or []:
        voice = quick02.catalog_item_voice(item)
        voices.append({
            "provider": item.get("provider") or catalog.get("provider") or "google",
            "model": item.get("model") or catalog.get("model") or args.model,
            "voice": voice,
            "voice_label": item.get("voice_label") or item.get("label") or voice,
            "voice_source": "system_catalog",
            "sample_audio_path": quick02.catalog_item_audio_rel(item),
        })
    return {
        "catalog_dir": str(cdir),
        "provider": catalog.get("provider"),
        "model": catalog.get("model"),
        "sample_text_id": catalog.get("sample_text_id"),
        "count": len(voices),
        "voices": voices,
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def percentile(values: list[float], percent: float, default: float = 0.0) -> float:
    if not values:
        return default
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    index = max(0.0, min(100.0, float(percent))) / 100.0 * (len(ordered) - 1)
    left = int(index)
    right = min(len(ordered) - 1, left + 1)
    frac = index - left
    return ordered[left] * (1.0 - frac) + ordered[right] * frac


def advanced_audio_features(quick02: Any, path: Path, speaking_rate_cps: float, target_duration: float = 0.0) -> dict[str, Any]:
    features = dict(quick02.audio_features(path, speaking_rate_cps))
    samples: list[float] = []
    rate = 0
    duration = safe_float(features.get("duration"))
    try:
        samples, rate, duration = quick02.read_wav_samples(path)
    except Exception:
        samples = []
    features["duration"] = duration or safe_float(features.get("duration"))
    if not samples or rate <= 0:
        features.update({
            "clipping_risk": 1.0 if not path.exists() else 0.0,
            "silence_ratio": 1.0 if not path.exists() else 0.0,
            "duration_error": 1.0 if target_duration > 0 else 0.0,
            "duration_fit_score": 0.0 if target_duration > 0 else 50.0,
            "energy_stability_score": 50.0,
            "warmth_ratio": 0.0,
            "roughness_proxy": 0.0,
            "nasality_proxy": 0.0,
            "sibilance_proxy": 0.0,
            "consonant_proxy": 0.0,
        })
        return features

    abs_samples = [abs(float(value)) for value in samples]
    clipping_risk = sum(1 for value in abs_samples if value >= 0.98) / max(1, len(abs_samples))
    frame_size = max(1, int(rate * 0.05))
    frame_rms: list[float] = []
    for start in range(0, len(samples), frame_size):
        frame = samples[start:start + frame_size]
        if not frame:
            continue
        frame_rms.append((sum(float(value) * float(value) for value in frame) / len(frame)) ** 0.5)
    rms_floor = max(0.004, percentile(frame_rms, 55, 0.0) * 0.40)
    silence_ratio = sum(1 for value in frame_rms if value < rms_floor) / max(1, len(frame_rms))
    mean_rms = sum(frame_rms) / max(1, len(frame_rms))
    rms_variance = sum((value - mean_rms) ** 2 for value in frame_rms) / max(1, len(frame_rms))
    rms_std = rms_variance ** 0.5
    energy_stability_score = max(0.0, min(100.0, 100.0 * (1.0 - min(1.0, rms_std / max(1e-6, mean_rms * 1.6)))))
    actual_duration = safe_float(features.get("duration"), duration)
    duration_error = abs(actual_duration - target_duration) / max(0.1, target_duration) if target_duration > 0 else 0.0
    duration_fit_score = max(0.0, min(100.0, 100.0 * (1.0 - min(1.0, duration_error))))
    centroid = safe_float(features.get("spectral_centroid"))
    zcr = safe_float(features.get("zero_crossing"))
    rms = safe_float(features.get("rms"))
    pitch = safe_float(features.get("pitch_hz"))
    spectral_peak = safe_float(features.get("spectral_peak_hz"))
    warmth_ratio = max(0.0, min(4.0, (rms * 100.0) / max(1.0, centroid / 120.0)))
    roughness_proxy = max(0.0, min(1.0, zcr * 18.0))
    nasality_proxy = max(0.0, min(2.0, spectral_peak / max(1.0, centroid)))
    sibilance_proxy = max(0.0, min(2.0, (centroid / max(1.0, rate / 2.0)) * (zcr * 12.0)))
    consonant_proxy = max(0.0, min(1.0, zcr * 24.0))
    pitch_range_hz = max(0.0, pitch * (0.25 + safe_float(features.get("pitch_confidence")) * 0.35)) if pitch > 0 else 0.0
    pitch_stability = max(0.0, min(100.0, 100.0 * safe_float(features.get("pitch_confidence"), 0.5)))
    features.update({
        "clipping_risk": float(clipping_risk),
        "silence_ratio": float(silence_ratio),
        "duration_error": float(duration_error),
        "duration_fit_score": float(duration_fit_score),
        "energy_stability_score": float(energy_stability_score),
        "warmth_ratio": float(warmth_ratio),
        "roughness_proxy": float(roughness_proxy),
        "nasality_proxy": float(nasality_proxy),
        "sibilance_proxy": float(sibilance_proxy),
        "consonant_proxy": float(consonant_proxy),
        "pitch_range_hz": float(pitch_range_hz),
        "pitch_stability": float(pitch_stability),
    })
    return features


def infer_age_label(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values).lower()
    if any(token in text for token in ("童", "child", "kid", "baby", "萝莉", "小孩")):
        return "child"
    if any(token in text for token in ("少年", "少女", "young", "boy", "girl", "妹妹", "小妹")):
        return "young"
    if any(token in text for token in ("老", "大爷", "大叔", "叔", "senior", "elder")):
        return "senior"
    if text.strip():
        return "adult"
    return ""


def style_score_for_candidate(item: dict[str, Any], reference_text: str) -> float:
    language = str(item.get("language") or item.get("lang") or "").lower()
    label_text = f"{item.get('voice_label') or ''} {item.get('label') or ''} {item.get('style') or ''}".lower()
    reference_has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in reference_text)
    if reference_has_cjk and language in {"zh", "zh-cn", "chinese", "mandarin", ""}:
        return 100.0
    if reference_has_cjk and any(token in label_text for token in ("粤语", "上海", "四川", "北京", "南京", "陕西", "闽南", "天津")):
        return 82.0
    if language:
        return 70.0
    return 80.0


def provider_readiness_score_for_item(item: dict[str, Any], audio_path: Path) -> float:
    provider = str(item.get("provider") or "").strip()
    model = str(item.get("model") or "").strip()
    voice = str(item.get("voice") or item.get("voice_id") or "").strip()
    return 100.0 if provider and model and voice and audio_path.exists() else 0.0


def row_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return (str(row.get("provider") or ""), str(row.get("model") or ""), str(row.get("voice") or ""))


def select_stage1_pool(rows: list[dict[str, Any]], stage1_count: int) -> list[dict[str, Any]]:
    limit = max(1, int(stage1_count or 1))
    ranked = sorted(rows, key=lambda row: float(row.get("stage1_score") or 0.0), reverse=True)
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    protected_keys: list[tuple[str, str, str]] = []

    def add(row: dict[str, Any], lane: str, *, protected: bool = False) -> None:
        key = row_identity(row)
        if key in selected:
            lanes = selected[key].setdefault("stage1_lane_sources", [])
            if lane not in lanes:
                lanes.append(lane)
            if protected and key not in protected_keys:
                protected_keys.append(key)
                selected[key]["stage1_lane_protected"] = True
            return
        clone = {**row, "stage1_lane_sources": [lane]}
        if protected:
            clone["stage1_lane_protected"] = True
            protected_keys.append(key)
        selected[key] = clone

    main_count = max(1, min(limit, int(round(limit * 0.70))))
    for row in ranked[:main_count]:
        add(row, "stage1_score_top")
    lane_count = max(1, int(round(limit * 0.10)))
    for row in sorted(rows, key=lambda row: float(row.get("dimension_scores", {}).get("pitch_score") or 0.0), reverse=True)[:lane_count]:
        add(row, "pitch_nearest_top", protected=True)
    for row in sorted(rows, key=lambda row: float(row.get("dimension_scores", {}).get("pace_score") or 0.0), reverse=True)[:lane_count]:
        add(row, "pace_nearest_top", protected=True)
    for row in sorted(rows, key=lambda row: (float(row.get("dimension_scores", {}).get("persona_score") or 0.0), float(row.get("stage1_score") or 0.0)), reverse=True)[:lane_count]:
        add(row, "same_gender_top", protected=True)
    providers = sorted({str(row.get("provider") or "") for row in rows if row.get("provider")})
    for provider in providers:
        provider_rows = [row for row in ranked if str(row.get("provider") or "") == provider]
        for row in provider_rows[:1]:
            add(row, "provider_quota_top", protected=True)
    for row in ranked:
        if len(selected) >= limit:
            break
        add(row, "stage1_score_fill")

    protected_pool = sorted(
        [selected[key] for key in protected_keys if key in selected],
        key=lambda row: float(row.get("stage1_score") or 0.0),
        reverse=True,
    )[:limit]
    pool_by_key = {row_identity(row): row for row in protected_pool}
    for row in ranked:
        if len(pool_by_key) >= limit:
            break
        key = row_identity(row)
        if key not in pool_by_key and key in selected:
            pool_by_key[key] = selected[key]
    pool = sorted(pool_by_key.values(), key=lambda row: float(row.get("stage1_score") or 0.0), reverse=True)[:limit]
    for index, row in enumerate(pool, 1):
        row["stage1_rank"] = index
        row["rank"] = index
    return pool


def safe_speechbrain_embedding(quick02: Any, path: Path, backend: Any | None, result: dict[str, Any] | None, *, scope: str, voice: str = "") -> Any | None:
    if backend is None:
        return None
    try:
        return quick02.speechbrain_embedding(path, backend)
    except Exception as exc:
        if result is not None:
            label = f"{scope}:{voice}" if voice else scope
            result.setdefault("warnings", []).append({
                "code": "speechbrain_embedding_failed",
                "message": f"SpeechBrain embedding failed for {label}; using acoustic fallback where possible: {exc}",
            })
        return None


def rank_voices(workspace: Path, args: AdvArgs, result: dict[str, Any] | None = None) -> dict[str, Any]:
    quick02 = load_quick02()
    validate_workspace(workspace)
    variables, final_items = load_inputs(workspace)
    sampled = sample_reference(workspace, args, result)
    reference_profile = sampled["reference_profile"]
    target_gender = reference_profile.get("gender_gate") or {}
    reference_text = str(reference_profile.get("dialogue") or "")
    reference_rate = quick02.count_cjk(reference_text) / max(0.1, float(reference_profile.get("selected_duration") or 16.0))
    cdir = catalog_dir(workspace, args)
    catalog = load_voice_catalog(quick02, cdir, args)

    resemblyzer_backend = quick02.load_resemblyzer_backend(result if result is not None else {"warnings": []})
    reference_audio = workspace / str(reference_profile.get("audio_path") or WORKING_REFERENCE_REL)
    reference_features = advanced_audio_features(quick02, reference_audio, reference_rate, float(reference_profile.get("selected_duration") or 16.0))
    reference_profile["score_schema_version"] = SCORE_SCHEMA_VERSION
    reference_profile["features"] = quick02.rounded_feature_map(reference_features)
    reference_profile["dimension_profile"] = {
        "pitch": {
            "pitch_hz": round(safe_float(reference_features.get("pitch_hz")), 3),
            "pitch_range_hz": round(safe_float(reference_features.get("pitch_range_hz")), 3),
            "pitch_stability": round(safe_float(reference_features.get("pitch_stability")), 3),
        },
        "pace": {
            "speaking_rate_cps": round(safe_float(reference_features.get("speaking_rate_cps")), 3),
            "pause_density": round(safe_float(reference_features.get("silence_ratio")), 3),
        },
        "texture": {
            "brightness": round(safe_float(reference_features.get("spectral_centroid")), 3),
            "warmth": round(safe_float(reference_features.get("warmth_ratio")), 3),
            "roughness": round(safe_float(reference_features.get("roughness_proxy")), 3),
            "nasality": round(safe_float(reference_features.get("nasality_proxy")), 3),
        },
        "quality": {
            "voice_activity_score": round(safe_float(sampled.get("sampling_audit", {}).get("score_parts", {}).get("voice_activity_score")), 3),
            "clipping_risk": round(safe_float(reference_features.get("clipping_risk")), 6),
            "sampling_score": round(safe_float(sampled.get("sampling_audit", {}).get("sampling_score")), 3),
        },
    }
    write_json(workspace / OUTPUT_REFERENCE_PROFILE_REL, reference_profile)
    reference_resemblyzer = quick02.resemblyzer_embedding(reference_audio, resemblyzer_backend)

    sample_text = str(catalog.get("sample_text") or "")
    sample_text_chars = quick02.count_cjk(sample_text)
    rows: list[dict[str, Any]] = []
    for item in catalog.get("voices") or []:
        voice = quick02.catalog_item_voice(item)
        audio_path = quick02.catalog_item_audio_path(cdir, item)
        raw_duration = float(item.get("raw_duration") or item.get("audio", {}).get("duration") or quick02.wav_duration(audio_path) or 16.0)
        catalog_rate = sample_text_chars / max(0.1, raw_duration)
        target_duration = float(reference_profile.get("selected_duration") or 16.0)
        tempo = (catalog_rate / reference_rate) if reference_rate > 0 and catalog_rate > 0 else 1.0
        features = advanced_audio_features(quick02, audio_path, catalog_rate, target_duration)
        candidate_resemblyzer = quick02.resemblyzer_embedding(audio_path, resemblyzer_backend)
        resemblyzer_score = quick02.cosine_score(reference_resemblyzer, candidate_resemblyzer)
        candidate_gender = quick02.catalog_voice_gender(item, features)
        gender_ok = quick02.gender_match(target_gender, candidate_gender)

        pitch_score = ratio_score(float(reference_features.get("pitch_hz") or 0.0), float(features.get("pitch_hz") or 0.0))
        pace_score = ratio_score(float(reference_features.get("speaking_rate_cps") or 0.0), float(features.get("speaking_rate_cps") or 0.0))
        brightness_score = ratio_score(float(reference_features.get("spectral_centroid") or 0.0), float(features.get("spectral_centroid") or 0.0))
        energy_score = ratio_score(float(reference_features.get("rms") or 0.0), float(features.get("rms") or 0.0))
        clarity_score = ratio_score(float(reference_features.get("zero_crossing") or 0.0), float(features.get("zero_crossing") or 0.0))
        warmth_score = ratio_score(float(reference_features.get("warmth_ratio") or 0.0), float(features.get("warmth_ratio") or 0.0))
        roughness_score = ratio_score(float(reference_features.get("roughness_proxy") or 0.0), float(features.get("roughness_proxy") or 0.0))
        nasality_score = ratio_score(float(reference_features.get("nasality_proxy") or 0.0), float(features.get("nasality_proxy") or 0.0))
        sibilance_score = ratio_score(float(reference_features.get("sibilance_proxy") or 0.0), float(features.get("sibilance_proxy") or 0.0))
        consonant_similarity_score = ratio_score(float(reference_features.get("consonant_proxy") or 0.0), float(features.get("consonant_proxy") or 0.0))
        consonant_quality_score = min(100.0, max(0.0, float(features.get("consonant_proxy") or 0.0) * 100.0))
        consonant_proxy_score = 0.65 * consonant_quality_score + 0.35 * consonant_similarity_score
        texture_score = build_texture_score(
            brightness_score=brightness_score,
            warmth_score=warmth_score,
            roughness_score=roughness_score,
            nasality_score=nasality_score,
        )
        articulation_score = build_articulation_score(
            clarity_score=clarity_score,
            consonant_proxy_score=consonant_proxy_score,
            sibilance_score=sibilance_score,
        )
        target_gender_label = str(target_gender.get("target_gender") or "")
        candidate_gender_label = str(candidate_gender.get("gender") or "")
        age_proxy_score = build_age_proxy_score(
            infer_age_label(reference_profile.get("dialogue"), target_gender.get("source")),
            infer_age_label(item.get("voice_label"), item.get("label"), item.get("style")),
        )
        pitch_band_score = build_pitch_band_score(
            float(reference_features.get("pitch_hz") or 0.0),
            float(features.get("pitch_hz") or 0.0),
            reference_gender=target_gender_label,
            candidate_gender=candidate_gender_label,
        )
        gender_score = 100.0 if gender_ok else 0.0
        persona_score = build_persona_score(gender_score=gender_score, age_proxy_score=age_proxy_score, pitch_band_score=pitch_band_score)
        style_score = style_score_for_candidate(item, reference_text)
        provider_readiness_score = provider_readiness_score_for_item({**item, "provider": item.get("provider") or catalog.get("provider"), "model": item.get("model") or catalog.get("model") or args.model}, audio_path)
        catalog_quality_penalty = quality_penalty_score(
            clipping_risk=safe_float(features.get("clipping_risk")),
            silence_ratio=safe_float(features.get("silence_ratio")),
            duration_error=safe_float(features.get("duration_error")),
            rms=safe_float(features.get("rms")),
        )
        stability_score = absolute_quality_score(
            energy_stability=safe_float(features.get("energy_stability_score"), 50.0),
            duration_fit=safe_float(features.get("duration_fit_score"), 100.0 if audio_path.exists() else 0.0),
            clipping_risk=safe_float(features.get("clipping_risk")),
        )
        stage1_base_score = build_stage1_score(
            resemblyzer_score=resemblyzer_score,
            pitch_score=pitch_score,
            pace_score=pace_score,
            brightness_score=brightness_score,
            gender_score=gender_score,
            catalog_quality_penalty=0.0,
        )
        stage1_score = build_stage1_score(
            resemblyzer_score=resemblyzer_score,
            pitch_score=pitch_score,
            pace_score=pace_score,
            brightness_score=brightness_score,
            gender_score=gender_score,
            catalog_quality_penalty=catalog_quality_penalty,
        )
        dimension_scores = rounded_scores({
            "timbre_score": build_timbre_score(scoring_mode=SCORING_DEGRADED, resemblyzer_score=resemblyzer_score, speechbrain_score=None, texture_score=texture_score),
            "pitch_score": pitch_score,
            "pace_score": pace_score,
            "articulation_score": articulation_score,
            "texture_score": texture_score,
            "persona_score": persona_score,
            "style_score": style_score,
            "energy_score": energy_score,
            "stability_score": stability_score,
        })
        timbre_rank_component = build_timbre_rank_component(
            resemblyzer_score=resemblyzer_score,
            brightness_score=brightness_score,
            roughness_score=roughness_score,
        )
        penalties = build_penalties(
            gender_match=gender_ok,
            pitch_score=pitch_score,
            pace_score=pace_score,
            catalog_quality_penalty=catalog_quality_penalty,
        )
        rows.append({
            "rank": 0,
            "score_schema_version": SCORE_SCHEMA_VERSION,
            "provider": item.get("provider") or catalog.get("provider") or "google",
            "model": item.get("model") or catalog.get("model") or args.model,
            "voice": voice,
            "voice_label": item.get("voice_label") or item.get("label") or voice,
            "voice_source": "system_catalog",
            "catalog_audio_abs": str(audio_path),
            "catalog_audio_path": relpath(audio_path, workspace),
            "raw_duration": round(raw_duration, 3),
            "target_duration": round(target_duration, 3),
            "fit_duration": round(float(quick02.wav_duration(audio_path) or raw_duration), 3),
            "tempo": round(float(tempo), 6),
            "tempo_source": "local_voice_catalog_match",
            "score": round(float(stage1_score), 6),
            "score_parts": rounded_scores({
                "scoring_mode": "stage1_resemblyzer_acoustic",
                "stage1_base_score": stage1_base_score,
                "stage1_score": stage1_score,
                "timbre_rank_component": timbre_rank_component,
                "resemblyzer_cosine": resemblyzer_score,
                "pitch_score": pitch_score,
                "pace_score": pace_score,
                "brightness_score": brightness_score,
                "warmth_score": warmth_score,
                "roughness_score": roughness_score,
                "nasality_score": nasality_score,
                "sibilance_score": sibilance_score,
                "energy_score": energy_score,
                "clarity_score": clarity_score,
                "articulation_score": articulation_score,
                "texture_score": texture_score,
                "stability_score": stability_score,
                "gender_score": gender_score,
                "persona_score": persona_score,
                "style_score": style_score,
                "provider_readiness_score": provider_readiness_score,
                "catalog_quality_penalty": catalog_quality_penalty,
            }),
            "target_gender": target_gender.get("target_gender"),
            "scoring_mode": "stage1_resemblyzer_acoustic",
            "scoring_mode_reason": "",
            "stage1_base_score": round(stage1_base_score, 6),
            "stage1_score": round(stage1_score, 6),
            "match_score": round(stage1_score),
            "scores": rounded_scores({
                "scoring_mode": "stage1_resemblyzer_acoustic",
                "stage1_base_score": stage1_base_score,
                "stage1_score": stage1_score,
                "resemblyzer_cosine": resemblyzer_score,
                "resemblyzer_score_normalized": normalize_cosine(resemblyzer_score),
                **dimension_scores,
            }),
            "dimension_scores": dimension_scores,
            "raw_scores": rounded_scores({
                "resemblyzer_cosine": resemblyzer_score,
                "speechbrain_cosine": None,
                "resemblyzer_score_normalized": normalize_cosine(resemblyzer_score),
                "speechbrain_score_normalized": None,
            }),
            "penalties": rounded_scores(penalties),
            "explanation": build_candidate_explanation(dimension_scores),
            "candidate_gender": candidate_gender,
            "gender_match": gender_ok,
            "exclude_reason": "" if gender_ok else f"gender_mismatch:{target_gender.get('target_gender')}!={candidate_gender.get('gender') or 'unknown'}",
            "features": quick02.rounded_feature_map(features),
            "catalog_index_item": item,
        })

    ranked_stage1_all = sorted(rows, key=lambda row: float(row.get("stage1_score") or 0.0), reverse=True)
    for index, row in enumerate(ranked_stage1_all, 1):
        row["catalog_rank"] = index
    stage1 = select_stage1_pool(ranked_stage1_all, max(1, int(args.stage1_count or 24)))

    speechbrain_backend = None if args.disable_speechbrain else quick02.load_speechbrain_backend(result if result is not None else {"warnings": []})
    reference_speechbrain = safe_speechbrain_embedding(
        quick02,
        reference_audio,
        speechbrain_backend,
        result,
        scope="reference",
    ) if speechbrain_backend is not None else None
    speechbrain_available = reference_speechbrain is not None and speechbrain_backend is not None
    base_scoring_reason = "" if speechbrain_available else "SpeechBrain disabled or unavailable"
    speechbrain_scored_count = 0
    speechbrain_fallback_count = 0
    stage2_rows: list[dict[str, Any]] = []
    for row in stage1:
        audio_path = Path(str(row.get("catalog_audio_abs") or ""))
        speechbrain_score = None
        if speechbrain_available:
            candidate_speechbrain = safe_speechbrain_embedding(
                quick02,
                audio_path,
                speechbrain_backend,
                result,
                scope="candidate",
                voice=str(row.get("voice") or row.get("voice_label") or ""),
            )
            speechbrain_score = quick02.cosine_score(reference_speechbrain, candidate_speechbrain)
            if speechbrain_score is None:
                speechbrain_fallback_count += 1
            else:
                speechbrain_scored_count += 1
        row_scoring_mode = SCORING_FULL if speechbrain_score is not None else SCORING_DEGRADED
        row_scoring_reason = "" if row_scoring_mode == SCORING_FULL else (
            "SpeechBrain candidate embedding unavailable; scored with acoustic fallback"
            if speechbrain_available
            else base_scoring_reason
        )
        score_parts = row.get("score_parts") if isinstance(row.get("score_parts"), dict) else {}
        dimension_scores = dict(row.get("dimension_scores") if isinstance(row.get("dimension_scores"), dict) else {})
        penalties = row.get("penalties") if isinstance(row.get("penalties"), dict) else {}
        stage1_prior_score = safe_float(row.get("stage1_base_score"), safe_float(row.get("stage1_score")))
        stage2_score = build_stage2_score(
            scoring_mode=row_scoring_mode,
            stage1_score=stage1_prior_score,
            resemblyzer_score=safe_float(row.get("raw_scores", {}).get("resemblyzer_cosine"), None) if isinstance(row.get("raw_scores"), dict) else None,
            speechbrain_score=speechbrain_score,
            pitch_score=safe_float(dimension_scores.get("pitch_score"), 50.0),
            pace_score=safe_float(dimension_scores.get("pace_score"), 50.0),
            brightness_score=safe_float(score_parts.get("brightness_score"), 50.0),
            energy_score=safe_float(dimension_scores.get("energy_score"), 50.0),
            clarity_score=safe_float(score_parts.get("clarity_score"), 50.0),
            stability_score=safe_float(dimension_scores.get("stability_score"), 50.0),
            texture_score=safe_float(dimension_scores.get("texture_score"), 50.0),
            articulation_score=safe_float(dimension_scores.get("articulation_score"), 50.0),
            persona_score=safe_float(dimension_scores.get("persona_score"), 50.0),
            style_score=safe_float(dimension_scores.get("style_score"), 50.0),
            provider_readiness_score=safe_float(score_parts.get("provider_readiness_score"), 100.0),
            roughness_score=safe_float(score_parts.get("roughness_score"), 50.0),
            penalties={key: safe_float(value) for key, value in penalties.items() if isinstance(value, (int, float))},
        )
        final_score = build_final_score(stage2_score=stage2_score)
        raw_scores = dict(row.get("raw_scores") if isinstance(row.get("raw_scores"), dict) else {})
        raw_scores["speechbrain_cosine"] = speechbrain_score
        raw_scores["speechbrain_score_normalized"] = normalize_cosine(speechbrain_score) if speechbrain_score is not None else None
        raw_scores["resemblyzer_score_normalized"] = normalize_cosine(raw_scores.get("resemblyzer_cosine"))
        dimension_scores["timbre_score"] = round(build_timbre_score(
            scoring_mode=row_scoring_mode,
            resemblyzer_score=raw_scores.get("resemblyzer_cosine"),
            speechbrain_score=speechbrain_score,
            texture_score=safe_float(dimension_scores.get("texture_score"), 50.0),
        ), 3)
        next_score_parts = {
            **score_parts,
            "scoring_mode": row_scoring_mode,
            "final_score": final_score,
            "stage1_prior_score": stage1_prior_score,
            "stage1_base_score": safe_float(row.get("stage1_base_score"), safe_float(row.get("stage1_score"))),
            "stage1_score": safe_float(row.get("stage1_score")),
            "stage2_score": stage2_score,
            "speechbrain_cosine": speechbrain_score,
            "speechbrain_score_normalized": raw_scores["speechbrain_score_normalized"],
        }
        next_row = {
            **row,
            "rank": 0,
            "score": round(float(stage2_score), 6),
            "match_score": round(stage2_score),
            "stage2_score": round(stage2_score, 6),
            "final_score": round(final_score, 6),
            "scoring_mode": row_scoring_mode,
            "scoring_mode_reason": row_scoring_reason,
            "score_parts": rounded_scores(next_score_parts),
            "scores": rounded_scores({
                "scoring_mode": row_scoring_mode,
                "final_score": final_score,
                "stage1_prior_score": stage1_prior_score,
                "stage1_base_score": safe_float(row.get("stage1_base_score"), safe_float(row.get("stage1_score"))),
                "stage1_score": safe_float(row.get("stage1_score")),
                "stage2_score": stage2_score,
                "resemblyzer_cosine": raw_scores.get("resemblyzer_cosine"),
                "speechbrain_cosine": speechbrain_score,
                "resemblyzer_score_normalized": raw_scores.get("resemblyzer_score_normalized"),
                "speechbrain_score_normalized": raw_scores.get("speechbrain_score_normalized"),
                **dimension_scores,
            }),
            "dimension_scores": rounded_scores(dimension_scores),
            "raw_scores": rounded_scores(raw_scores),
            "explanation": build_candidate_explanation(dimension_scores),
        }
        stage2_rows.append(next_row)

    ranked_stage2 = sorted(stage2_rows, key=lambda row: float(row.get("stage2_score") or row.get("match_score") or 0.0), reverse=True)
    for index, row in enumerate(ranked_stage2, 1):
        row["rank"] = index
        row["stage2_rank"] = index
    stage2 = ranked_stage2[: max(1, int(args.stage2_count or 6))]
    scoring_mode = SCORING_FULL if speechbrain_scored_count > 0 else SCORING_DEGRADED
    if speechbrain_scored_count > 0 and speechbrain_fallback_count > 0:
        scoring_reason = f"SpeechBrain scored {speechbrain_scored_count} candidates; {speechbrain_fallback_count} candidates used acoustic fallback"
    elif speechbrain_scored_count > 0:
        scoring_reason = ""
    elif speechbrain_available:
        scoring_reason = "SpeechBrain reference was available but candidate embeddings failed; all candidates used acoustic fallback"
    else:
        scoring_reason = base_scoring_reason
    board = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "score_schema_version": SCORE_SCHEMA_VERSION,
        "ranking_strategy": "two_stage_high_recall",
        "catalog_dir": str(cdir),
        "scoring_mode": scoring_mode,
        "scoring_mode_reason": scoring_reason,
        "available_backends": {
            "resemblyzer": reference_resemblyzer is not None,
            "speechbrain": speechbrain_scored_count > 0,
            "speechbrain_reference": speechbrain_available,
            "acoustic": True,
        },
        "reference_profile": reference_profile,
        "stage1": stage1,
        "stage2": stage2,
        "recommended": stage2[: max(1, int(args.final_count or 3))],
        "stage_counts": {
            "catalog_total": len(rows),
            "stage1_pool": len(stage1),
            "stage2_ranked": len(ranked_stage2),
            "recommended": min(len(stage2), max(1, int(args.final_count or 3))),
            "speechbrain_scored": speechbrain_scored_count,
            "speechbrain_fallback": speechbrain_fallback_count,
        },
        "updated_at": now_iso(),
    }
    write_json(workspace / OUTPUT_STAGE1_REL, {
        "stage": "stage1",
        "score_schema_version": SCORE_SCHEMA_VERSION,
        "scoring_mode": "stage1_resemblyzer_acoustic",
        "candidate_count_total": len(rows),
        "candidate_count_stage1": len(stage1),
        "ranked": stage1,
        "updated_at": now_iso(),
    })
    write_json(workspace / OUTPUT_STAGE2_REL, board)
    write_json(workspace / INTERACTIVE_RANKING_REL, board)
    if result is not None:
        result["local_match_backend"] = board["available_backends"]
        result["scoring_mode"] = scoring_mode
        result["scoring_mode_reason"] = scoring_reason
        result.setdefault("outputs", {})["ranking_board"] = INTERACTIVE_RANKING_REL
        result.setdefault("counts", {})["ranked_candidates"] = len(ranked_stage2)
        result.setdefault("counts", {})["stage1_pool"] = len(stage1)
    return board


def generate_adv_model_candidate(
    quick02: Any,
    workspace: Path,
    args: AdvArgs,
    provider_config: dict[str, Any],
    provider: str,
    model: str,
    variables: dict[str, Any],
    scene_profile: dict[str, Any],
    reference_profile: dict[str, Any],
    reference_text: str,
    row: dict[str, Any],
    rank: int,
    target_duration: float,
) -> tuple[dict[str, Any], int]:
    voice = str(row.get("voice") or "").strip()
    variant = "closest_reference" if rank == 1 else "natural_selfie"
    safe_voice = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in voice) or f"voice_{rank}"
    prompt_rel = f"{PROMPT_DIR_REL}/final_candidate_{rank:03d}_{safe_voice}_prompt.txt"
    planner_rel = f"{PROMPT_DIR_REL}/final_candidate_{rank:03d}_{safe_voice}_planner_prompt.md"
    raw_rel = f"{WORKING_RAW_DIR_REL}/final_candidate_{rank:03d}_{safe_voice}_raw.wav"
    fit_rel = f"{WORKING_FIT_DIR_REL}/final_candidate_{rank:03d}_{safe_voice}_fit.wav"
    prompt_path = workspace / prompt_rel
    raw_path = workspace / raw_rel
    fit_path = workspace / fit_rel
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_text, prompt_meta = plan_candidate_prompt(
        quick02,
        workspace,
        args,
        variables,
        provider,
        model,
        voice,
        variant,
        scene_profile,
        reference_profile,
        reference_text,
        row,
        planner_rel,
    )
    prompt_path.write_text(prompt_text, encoding="utf-8")
    model_calls = 1 if prompt_meta.get("prompt_model_call_made") else 0
    if raw_path.exists() and raw_path.stat().st_size > 0 and not args.force:
        tts_meta = {"provider": provider, "model": model, "voice": voice, "duration": quick02.media_duration(raw_path), "cached": True}
    else:
        tts_meta = call_provider_tts(
            quick02,
            str(provider_config.get("api_key") or ""),
            provider,
            model,
            voice,
            prompt_path,
            raw_path,
            workspace=workspace,
            asset_key=f"adv_final_candidate_{rank:03d}_{safe_voice}",
            provider_extra=provider_config.get("extra") if isinstance(provider_config.get("extra"), dict) else {},
        )
        model_calls += 1
    fit_meta = quick02.fit_audio_to_duration(raw_path, fit_path, target_duration)
    session_path = workspace / session_candidate_path(rank)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fit_path, session_path)
    raw_duration = float(fit_meta.get("raw_duration") or quick02.media_duration(raw_path) or target_duration)
    generated = {
        **row,
        "catalog_rank": row.get("catalog_rank") or row.get("rank"),
        "catalog_score": row.get("score"),
        "catalog_tempo_prior": row.get("tempo"),
        "prompt": prompt_text,
        "prompt_path": prompt_rel,
        **prompt_meta,
        "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
        "raw_audio": raw_rel,
        "fit_audio": fit_rel,
        "tts_meta": tts_meta,
        "gemini_meta": tts_meta if provider in {"google", "gemini"} else {},
        "raw_duration": round(raw_duration, 3),
        "target_duration": round(float(target_duration), 3),
        "fit_duration": round(float(fit_meta.get("fit_duration") or quick02.media_duration(fit_path) or target_duration), 3),
        "tempo": round(float(fit_meta.get("tempo") or (raw_duration / target_duration if target_duration > 0 else 1.0)), 6),
        "tempo_source": "measured_after_raw_tts_generation",
        "model_call_made": bool(model_calls),
    }
    return generated, model_calls


def build_quick_compatible_payload_from_ranking(workspace: Path, args: AdvArgs, variables: dict[str, Any], ranking: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    quick02 = load_quick02()
    recommended = [row for row in ranking.get("recommended", []) if isinstance(row, dict)]
    final_count = max(1, int(args.final_count or 3))
    stage2_rows = [row for row in ranking.get("stage2", []) if isinstance(row, dict)]
    generation_pool = stage2_rows or recommended
    if len(generation_pool) < final_count:
        raise BlockedError("voice_catalog_candidates_too_small", f"Only {len(generation_pool)} candidates were ranked by 03_03; need {final_count}.")
    reference_profile = ranking.get("reference_profile") if isinstance(ranking.get("reference_profile"), dict) else {}
    reference_text = str(reference_profile.get("dialogue") or "")
    target_duration = float(reference_profile.get("selected_duration") or args.reference_duration or 16.0)
    selected_range = reference_profile.get("selected_range") if isinstance(reference_profile.get("selected_range"), dict) else {}
    window = {
        "start": selected_range.get("start", args.reference_start),
        "end": selected_range.get("end", float(selected_range.get("start") or args.reference_start or 0.0) + target_duration),
        "duration": target_duration,
    }
    scene_profile = quick02.load_scene_profile(workspace, variables, reference_text)
    provider_configs: dict[tuple[str, str], dict[str, Any]] = {}
    final_rows = []
    model_calls = 0
    generation_warnings: list[dict[str, Any]] = []
    for source_index, row in enumerate(generation_pool, 1):
        if len(final_rows) >= final_count:
            break
        provider = "google" if str(row.get("provider") or "google").strip().lower() == "gemini" else str(row.get("provider") or "google").strip().lower()
        model = str(row.get("model") or args.model).strip()
        cache_key = (provider, model)
        if cache_key not in provider_configs:
            provider_configs[cache_key] = load_provider_tts_config(quick02, workspace, args, provider, model)
        provider_config = provider_configs[cache_key]
        if not str(provider_config.get("api_key") or "").strip():
            raise BlockedError("tts_api_key_missing", f"No enabled TTS API key found for provider={provider}, model={model}.")
        output_rank = len(final_rows) + 1
        try:
            generated_row, calls = generate_adv_model_candidate(quick02, workspace, args, provider_config, provider, model, variables, scene_profile, reference_profile, reference_text, row, output_rank, target_duration)
        except BlockedError:
            raise
        except Exception as exc:
            voice = str(row.get("voice") or "").strip()
            source_rank = row.get("stage2_rank") or row.get("rank") or source_index
            generation_warnings.append({
                "code": "tts_candidate_generation_failed",
                "message": f"Skipped voice={voice or 'unknown'} after TTS generation failed: {str(exc)[:500]}",
                "voice": voice,
                "provider": provider,
                "model": model,
                "source_rank": source_rank,
            })
            continue
        generated_row["generation_source_rank"] = row.get("stage2_rank") or row.get("rank") or source_index
        final_rows.append(generated_row)
        model_calls += calls
    if generation_warnings:
        result.setdefault("warnings", []).extend(generation_warnings)
    if len(final_rows) < final_count:
        last_warning = generation_warnings[-1] if generation_warnings else {}
        last_message = str(last_warning.get("message") or "no successful TTS candidates")
        raise RuntimeError(f"Only generated {len(final_rows)} of {final_count} QuickAdv TTS candidates. {last_message}")
    payload = quick02.build_final_payload(window, reference_profile, final_rows)
    for candidate, row in zip(payload.get("candidates") or [], final_rows):
        candidate["tts_meta"] = row.get("tts_meta") or {}
        candidate["gemini_meta"] = row.get("gemini_meta") or {}
        candidate["prompt_source"] = row.get("prompt_source") or candidate.get("prompt_source") or "rule_fallback"
        candidate["planner_prompt_path"] = row.get("planner_prompt_path") or ""
        candidate["planner_response_path"] = row.get("planner_response_path") or ""
        candidate["planner_reason"] = row.get("planner_reason") or ""
        candidate["planner_error"] = row.get("planner_error") or ""
        candidate["score_schema_version"] = row.get("score_schema_version") or SCORE_SCHEMA_VERSION
        candidate["match_score"] = row.get("match_score")
        candidate["scores"] = row.get("scores") or candidate.get("score_parts") or {}
        candidate["dimension_scores"] = row.get("dimension_scores") or {}
        candidate["raw_scores"] = row.get("raw_scores") or {}
        candidate["penalties"] = row.get("penalties") or {}
        candidate["explanation"] = row.get("explanation") or {}
        candidate["stage1_rank"] = row.get("stage1_rank")
        candidate["stage1_score"] = row.get("stage1_score")
        candidate["stage2_rank"] = row.get("stage2_rank")
        candidate["stage2_score"] = row.get("stage2_score")
        candidate["scoring_mode"] = row.get("scoring_mode") or payload.get("ranking_policy", {}).get("scoring_mode") or SCORING_DEGRADED
        candidate["scoring_mode_reason"] = row.get("scoring_mode_reason") or ""
        candidate["reason"] = "Voice was pre-ranked by local catalog similarity, then regenerated by the selected TTS provider with the real selected SRT text and fitted to the target duration."
    payload["tool"] = TOOL_NAME
    payload["tool_version"] = TOOL_VERSION
    payload["scene_profile"] = scene_profile
    payload.setdefault("sample_policy", {})["reason"] = "03_03 uses its own stage2 ranking to select final voices, then generates bounded TTS samples for those voices."
    result.setdefault("counts", {})["model_calls"] = model_calls
    result.setdefault("created_files", []).extend([
        session_candidate_path(rank)
        for rank in range(1, len(final_rows) + 1)
    ])
    return payload


def build_adv_candidates_from_quick(workspace: Path, quick_payload: dict[str, Any], ranking: dict[str, Any], args: AdvArgs) -> dict[str, Any]:
    quick_candidates = quick_payload.get("candidates") if isinstance(quick_payload.get("candidates"), list) else []
    ranked_by_voice = {str(row.get("voice") or ""): row for row in ranking.get("stage2", []) if isinstance(row, dict)}
    candidates = []
    for index, item in enumerate(quick_candidates, 1):
        voice = str(item.get("voice") or "")
        ranked = ranked_by_voice.get(voice, {})
        scores = ranked.get("scores") if isinstance(ranked.get("scores"), dict) else {}
        match_score = ranked.get("match_score")
        if match_score is None:
            try:
                raw_score = float(item.get("score") or 0.0)
            except (TypeError, ValueError):
                raw_score = 0.0
            match_score = round(raw_score * 100.0) if raw_score <= 1.0 else round(raw_score)
        try:
            match_score_value = round(float(match_score))
        except (TypeError, ValueError):
            match_score_value = 0
        candidates.append({
            **item,
            "rank": index,
            "candidate_id": item.get("candidate_id") or f"tts_{index:03d}",
            "voice_source": "system_catalog",
            "score": match_score_value,
            "match_score": match_score_value,
            "scores": scores or {
                "scoring_mode": ranking.get("scoring_mode") or SCORING_DEGRADED,
                "final_score": float(match_score_value),
            },
            "scoring_mode": ranking.get("scoring_mode") or SCORING_DEGRADED,
            "scoring_mode_reason": ranking.get("scoring_mode_reason") or "",
            "preview_attempt_id": "",
        })
    selected = candidates[0] if candidates else {}
    return {
        "schema_version": "analysis_v1_tts_builder_adv_candidates_0.1",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "source_tool_dir": TOOL_DIR_NAME,
        "sample_policy": quick_payload.get("sample_policy") or {},
        "reference_audio_profile": ranking.get("reference_profile") or quick_payload.get("reference_audio_profile") or {},
        "ranking_policy": {
            "stage1": f"resemblyzer_top_{args.stage1_count}",
            "stage2": f"speechbrain_top_{args.stage2_count}" if ranking.get("scoring_mode") == SCORING_FULL else f"resemblyzer_acoustic_top_{args.stage2_count}",
            "final": f"tts_sample_top_{args.final_count}",
            "scoring_mode": ranking.get("scoring_mode") or SCORING_DEGRADED,
            "scoring_mode_reason": ranking.get("scoring_mode_reason") or "",
        },
        "scene_profile": quick_payload.get("scene_profile") or {},
        "selected_candidate_id": selected.get("candidate_id", ""),
        "selected_candidate": selected,
        "candidates": candidates,
        "created_at": now_iso(),
    }


def state(workspace: Path, args: AdvArgs) -> dict[str, Any]:
    ensure_dirs(workspace)
    reference_profile = read_json(workspace / OUTPUT_REFERENCE_PROFILE_REL) if (workspace / OUTPUT_REFERENCE_PROFILE_REL).exists() else None
    sampling_audit = read_json(workspace / OUTPUT_SAMPLING_AUDIT_REL) if (workspace / OUTPUT_SAMPLING_AUDIT_REL).exists() else None
    ranking_board = read_json(workspace / INTERACTIVE_RANKING_REL) if (workspace / INTERACTIVE_RANKING_REL).exists() else None
    final_candidates = read_json(workspace / SESSION_TTS_FINAL_REL) if (workspace / SESSION_TTS_FINAL_REL).exists() else None
    cloned_voices = read_json(workspace / SESSION_CLOUD_CLONES_REL) if (workspace / SESSION_CLOUD_CLONES_REL).exists() else {"clones": []}
    payload = {
        "ok": True,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workspace_dir": str(workspace),
        "reference": {
            "audio_path": SESSION_AUDIO_REFERENCE_REL,
            "profile_exists": bool(reference_profile),
            "profile": reference_profile,
            "sampling_audit": sampling_audit,
        },
        "ranking_board": ranking_board,
        "cloned_voices": cloned_voices.get("clones", []) if isinstance(cloned_voices, dict) else [],
        "final_candidates": final_candidates,
        "updated_at": now_iso(),
    }
    write_json(workspace / INTERACTIVE_STATE_REL, payload)
    return payload


def run_full(workspace: Path, args: AdvArgs) -> dict[str, Any]:
    result = base_result(workspace, args)
    session_tts_snapshot: dict[str, bytes] = {}
    try:
        ensure_dirs(workspace)
        if args.resume and (workspace / SESSION_TTS_FINAL_REL).exists() and not args.force:
            final_payload = read_json(workspace / SESSION_TTS_FINAL_REL)
            result["outputs"]["tts_builder_candidates"] = SESSION_TTS_FINAL_REL
            result["counts"]["final_candidates"] = len(final_payload.get("candidates") or [])
            result["warnings"].append({"code": "reused_completed_output", "message": "Existing TTS Builder QuickAdv candidates were reused."})
        else:
            if args.force:
                session_tts_snapshot = snapshot_session_tts_outputs(workspace)
            validate_workspace(workspace)
            variables, _final_items = load_inputs(workspace)
            ranking = rank_voices(workspace, args, result)
            quick_payload = build_quick_compatible_payload_from_ranking(workspace, args, variables, ranking, result)
            final_payload = build_adv_candidates_from_quick(workspace, quick_payload, ranking, args)
            write_json(workspace / OUTPUT_FINAL_REL, final_payload)
            write_json(workspace / SESSION_TTS_FINAL_REL, final_payload)
            write_json(workspace / WORKING_STATE_REL, {
                "tool": TOOL_NAME,
                "status": "completed",
                "phase": "finalize",
                "outputs": {"tts_builder_candidates": SESSION_TTS_FINAL_REL},
                "updated_at": now_iso(),
            })
            result["outputs"].update({
                "tts_builder_candidates": SESSION_TTS_FINAL_REL,
                "tts_builder_adv_candidates": OUTPUT_FINAL_REL,
                "report": REPORT_RESULT_REL,
            })
            result["counts"].update({
                "final_candidates": len(final_payload.get("candidates") or []),
            })
    except BlockedError as exc:
        result["status"] = "blocked"
        result["ok"] = False
        result.setdefault("blocked_reasons", []).append({"code": exc.code, "message": exc.message})
    except Exception as exc:
        result["status"] = "failed"
        result["ok"] = False
        result.setdefault("warnings", []).append({"code": "unexpected_error", "message": str(exc)})
    if result.get("status") != "completed" and args.force:
        try:
            restore_session_tts_outputs(workspace, session_tts_snapshot, result)
        except Exception as exc:
            result.setdefault("warnings", []).append({"code": "session_tts_restore_failed", "message": str(exc)})
    if result.get("status") == "completed":
        result["ok"] = True
    result["updated_at"] = now_iso()
    try:
        write_json(workspace / REPORT_RESULT_REL, result)
    except Exception:
        pass
    return result
