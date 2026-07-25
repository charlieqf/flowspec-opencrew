from __future__ import annotations

import json
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from opcrew_backend.context import AppContext
from opcrew_backend.services.provider_resolver import resolve_endpoint


CONFIG_TABLE = "tool_asr_provider_configs"
DEFAULT_CONFIG_NAME = "default_asr_provider"
LEGACY_CONFIG_NAMES = ("aliyun_fun_asr_default", "local_whisper_default")
DEFAULT_API_URL = "dashscope://audio/asr/transcription"
LEGACY_OPENAI_COMPATIBLE_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/audio/transcriptions"
RECORDING_FILE_ASR_MODELS = {"fun-asr", "fun-asr-2025-08-25"}
RECORDING_FILE_SAMPLE_URL = "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_female2.wav"


def normalize_model(model: str) -> str:
    return model


def normalize_api_url(api_url: str) -> str:
    return DEFAULT_API_URL if api_url == LEGACY_OPENAI_COMPATIBLE_API_URL else api_url


class ASRConfigSavePayload(BaseModel):
    config_name: str = DEFAULT_CONFIG_NAME
    provider: str
    model: str
    language: str = "zh"
    api_url: str = DEFAULT_API_URL
    api_key: str = ""
    enabled: bool = True


class ASRConnectionTestPayload(BaseModel):
    provider: str
    model: str


def default_api_key_ref(provider: str) -> str:
    return "aliyun_bailian_fun_asr_key" if provider == "aliyun_bailian_fun_asr" else f"{provider}_key"


def connection_result(ok: bool, message: str, detail: str = "") -> dict[str, Any]:
    return {"ok": ok, "status": "success" if ok else "failed", "message": message, "detail": detail}


def request_json(url: str, api_key: str, timeout: int = 12) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            try:
                return {"status": int(res.status), "body": json.loads(body) if body else {}}
            except json.JSONDecodeError:
                return {"status": int(res.status), "body": body[:1000]}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def sample_rate_for_model(model: str) -> int:
    return 8000 if "flash-8k" in model else 16000


def write_probe_wav(path: Path, sample_rate: int) -> None:
    frames = b"\x00\x00" * max(1, int(sample_rate * 0.25))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)


def model_options() -> list[dict[str, str]]:
    aliyun_models = [
        {
            "model": "fun-asr",
            "label": "fun-asr（录音文件识别 · 稳定版）",
            "description": "Fun-ASR 录音文件识别稳定版，适合已录制音视频转写、字幕生成和长音频离线处理。",
            "api_url": DEFAULT_API_URL,
        },
        {
            "model": "fun-asr-2025-08-25",
            "label": "fun-asr-2025-08-25（录音文件识别 · 快照版）",
            "description": "Fun-ASR 录音文件识别快照版，适合需要固定版本行为的离线转写任务。",
            "api_url": DEFAULT_API_URL,
        },
        {
            "model": "fun-asr-realtime",
            "label": "fun-asr-realtime（稳定版）",
            "description": "Fun-ASR-Realtime 稳定版，当前等同 fun-asr-realtime-2025-11-07，适合 16k 中文/多语种实时识别。",
            "api_url": "dashscope://audio/asr/recognition",
        },
        {
            "model": "fun-asr-realtime-2026-02-28",
            "label": "fun-asr-realtime-2026-02-28（快照版）",
            "description": "Fun-ASR-Realtime 2026-02-28 快照版，适合需要固定版本行为的 16k 识别任务。",
            "api_url": "dashscope://audio/asr/recognition",
        },
        {
            "model": "fun-asr-flash-8k-realtime",
            "label": "fun-asr-flash-8k-realtime（稳定版）",
            "description": "Fun-ASR-Flash 8k Realtime 稳定版，当前等同 fun-asr-flash-8k-realtime-2026-01-28，适合 8k 音频识别。",
            "api_url": "dashscope://audio/asr/recognition",
        },
        {
            "model": "fun-asr-flash-8k-realtime-2026-01-28",
            "label": "fun-asr-flash-8k-realtime-2026-01-28（快照版）",
            "description": "Fun-ASR-Flash 8k Realtime 2026-01-28 快照版，适合需要固定版本行为的 8k 音频识别。",
            "api_url": "dashscope://audio/asr/recognition",
        },
    ]
    return [
        *[
            {
                "provider": "aliyun_bailian_fun_asr",
                "model": item["model"],
                "label": item["label"],
                "description": item["description"],
                "api_url": item["api_url"],
            }
            for item in aliyun_models
        ],
        {
            "provider": "local_whisper",
            "model": "small",
            "label": "Local Whisper small",
            "description": "本地 Whisper small，用于离线兜底或开发验证。",
            "api_url": "",
        },
    ]


def ensure_table(ctx: AppContext) -> None:
    with ctx.engine.begin() as conn:
        conn.execute(text(f"""
CREATE TABLE IF NOT EXISTS {CONFIG_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  priority INTEGER NOT NULL DEFAULT 100,
  model TEXT NOT NULL DEFAULT 'small',
  language TEXT DEFAULT 'zh',
  api_url TEXT,
  api_key_ciphertext TEXT,
  api_key_ref TEXT,
  extra_json TEXT NOT NULL DEFAULT '{{}}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""))


def row_to_public(ctx: AppContext, row: Any | None) -> dict[str, Any]:
    if row is None:
        return {
            "config_name": DEFAULT_CONFIG_NAME,
            "provider": "aliyun_bailian_fun_asr",
            "model": "fun-asr",
            "language": "zh",
            "api_url": DEFAULT_API_URL,
            "enabled": True,
            "has_api_key": False,
            "api_key_ref": "aliyun_bailian_fun_asr_key",
            "updated_at": None,
        }
    mapping = row._mapping
    updated_at = mapping.get("updated_at")
    if isinstance(updated_at, datetime):
        updated_at_value: int | None = int(updated_at.timestamp() * 1000)
    elif updated_at is None:
        updated_at_value = None
    else:
        try:
            updated_at_value = int(updated_at)
        except (TypeError, ValueError):
            updated_at_value = None
    provider = mapping.get("provider") or "aliyun_bailian_fun_asr"
    api_key_ref = mapping.get("api_key_ref") or default_api_key_ref(str(provider))
    return {
        "config_name": DEFAULT_CONFIG_NAME,
        "provider": provider,
        "model": normalize_model(mapping.get("model") or "fun-asr"),
        "language": mapping.get("language") or "zh",
        "api_url": normalize_api_url(mapping.get("api_url") or DEFAULT_API_URL),
        "enabled": bool(mapping.get("enabled")),
        "has_api_key": bool(str(mapping.get("api_key_ciphertext") or "").strip()) or ctx.secret_store.has(str(api_key_ref)),
        "api_key_ref": api_key_ref,
        "updated_at": updated_at_value,
    }


def load_stored_key(ctx: AppContext, provider: str) -> str:
    ensure_table(ctx)
    with ctx.engine.begin() as conn:
        row = conn.execute(
            text(f"""
SELECT name, api_key_ref, api_key_ciphertext
FROM {CONFIG_TABLE}
WHERE provider = :provider
ORDER BY (name = :default_name) DESC, enabled DESC, priority ASC, updated_at DESC
LIMIT 1
"""),
            {"provider": provider, "default_name": DEFAULT_CONFIG_NAME},
        ).first()
    if not row:
        return ""
    mapping = row._mapping
    ref = str(mapping.get("api_key_ref") or f"{provider}_key")
    stored = ctx.secret_store.get(ref)
    if stored:
        return stored
    legacy = str(mapping.get("api_key_ciphertext") or "").strip()
    if legacy:
        ctx.secret_store.set(ref, legacy)
        with ctx.engine.begin() as conn:
            conn.execute(
                text(f"UPDATE {CONFIG_TABLE} SET api_key_ref = :ref, api_key_ciphertext = NULL, updated_at = now() WHERE name = :name AND provider = :provider"),
                {"ref": ref, "name": str(mapping.get("name") or DEFAULT_CONFIG_NAME), "provider": provider},
            )
        return legacy
    return ""


def test_asr_connection(provider: str, model: str, api_key: str) -> dict[str, Any]:
    if provider == "local_whisper":
        return connection_result(True, "Local model configured", "Local Whisper does not require an API Key.")
    if not api_key:
        return connection_result(False, "API Key missing", "Save this ASR provider config before testing the connection.")
    try:
        if provider == "aliyun_bailian_fun_asr":
            try:
                import dashscope  # type: ignore
                from dashscope.audio.asr import Recognition, Transcription  # type: ignore
            except ImportError as exc:
                raise RuntimeError("Python package 'dashscope' is not available; install backend requirements before testing Aliyun Bailian ASR.") from exc
            dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"
            dashscope.api_key = api_key
            if model in RECORDING_FILE_ASR_MODELS:
                language_hints = ["zh"] if model in {"fun-asr", "fun-asr-2025-08-25"} else None
                kwargs = {"language_hints": language_hints} if language_hints else {}
                task_response = Transcription.async_call(
                    model=model,
                    file_urls=[RECORDING_FILE_SAMPLE_URL],
                    api_key=api_key,
                    **kwargs,
                )
                status_code = getattr(task_response, "status_code", None)
                if status_code != HTTPStatus.OK:
                    output = getattr(task_response, "output", None)
                    message = getattr(output, "message", "") if output is not None else ""
                    raise RuntimeError(f"DashScope Transcription submit failed: {status_code} {message}".strip())
                output = getattr(task_response, "output", None)
                task_id = getattr(output, "task_id", "") if output is not None else ""
                if not task_id:
                    raise RuntimeError("DashScope Transcription submit did not return task_id.")
                return connection_result(True, "Connection verified", f"{provider}/{model} accepted the saved database key through DashScope Transcription SDK.")
            sample_rate = sample_rate_for_model(model)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
                write_probe_wav(Path(tmp.name), sample_rate)
                recognition = Recognition(model=model, format="wav", sample_rate=sample_rate, callback=None)
                result = recognition.call(tmp.name, api_key=api_key)
                status_code = getattr(result, "status_code", None)
                if str(status_code) not in {"200", "HTTPStatus.OK"}:
                    code = result.get("code", "") if isinstance(result, dict) else getattr(result, "code", "")
                    message = result.get("message", "") if isinstance(result, dict) else getattr(result, "message", "")
                    raise RuntimeError(f"DashScope Recognition failed: {status_code} {code} {message}".strip())
        else:
            return connection_result(False, "Unsupported provider", f"No connection test is configured for {provider}.")
    except Exception as exc:
        return connection_result(False, "Connection failed", str(exc))
    return connection_result(True, "Connection verified", f"{provider}/{model} is reachable with the saved database key.")


def build_asr_config_router(ctx: AppContext) -> APIRouter:
    router = APIRouter(prefix="/api/setup/asr", tags=["asr-config"])

    @router.get("/config")
    def get_config() -> dict[str, Any]:
        ensure_table(ctx)
        with ctx.engine.begin() as conn:
            row = conn.execute(
                text(f"""
SELECT *
FROM {CONFIG_TABLE}
WHERE enabled = true
ORDER BY (name = :default_name) DESC, priority ASC, updated_at DESC, id ASC
LIMIT 1
"""),
                {"default_name": DEFAULT_CONFIG_NAME},
            ).first()
        return {"config": row_to_public(ctx, row), "models": model_options()}

    @router.put("/config")
    def save_config(payload: ASRConfigSavePayload) -> dict[str, Any]:
        ensure_table(ctx)
        selected = next((item for item in model_options() if item["provider"] == payload.provider and item["model"] == payload.model), None)
        api_url = payload.api_url.strip() or (selected or {}).get("api_url") or ""
        api_key = payload.api_key.strip()
        config_name = DEFAULT_CONFIG_NAME
        with ctx.engine.begin() as conn:
            api_key_ref = default_api_key_ref(payload.provider)
            existing = conn.execute(
                text(f"""
SELECT api_key_ref, api_key_ciphertext
FROM {CONFIG_TABLE}
WHERE name IN (:name, :legacy_aliyun_name, :legacy_local_name)
ORDER BY (name = :name) DESC, enabled DESC, updated_at DESC
LIMIT 1
"""),
                {
                    "name": config_name,
                    "legacy_aliyun_name": LEGACY_CONFIG_NAMES[0],
                    "legacy_local_name": LEGACY_CONFIG_NAMES[1],
                },
            ).first()
            legacy_key = str(existing._mapping.get("api_key_ciphertext") or "").strip() if existing else ""
            if api_key:
                ctx.secret_store.set(api_key_ref, api_key)
            elif legacy_key and not ctx.secret_store.has(api_key_ref):
                ctx.secret_store.set(api_key_ref, legacy_key)
            conn.execute(text(f"UPDATE {CONFIG_TABLE} SET enabled = false, priority = 100, updated_at = now()"))
            conn.execute(
                text(f"""
INSERT INTO {CONFIG_TABLE} (name, provider, enabled, priority, model, language, api_url, api_key_ciphertext, api_key_ref, extra_json, created_at, updated_at)
VALUES (:name, :provider, :enabled, 5, :model, :language, :api_url, :api_key, :api_key_ref, :extra_json, now(), now())
ON CONFLICT (name) DO UPDATE SET
  provider = EXCLUDED.provider,
  enabled = EXCLUDED.enabled,
  priority = EXCLUDED.priority,
  model = EXCLUDED.model,
  language = EXCLUDED.language,
  api_url = EXCLUDED.api_url,
  api_key_ciphertext = NULL,
  api_key_ref = EXCLUDED.api_key_ref,
  extra_json = EXCLUDED.extra_json,
  updated_at = EXCLUDED.updated_at
"""),
                {
                    "name": config_name,
                    "provider": payload.provider,
                    "enabled": payload.enabled,
                    "model": payload.model,
                    "language": payload.language,
                    "api_url": api_url,
                    "api_key": None,
                    "api_key_ref": api_key_ref,
                    "extra_json": json.dumps({"timeout_seconds": 300}, ensure_ascii=False),
                },
            )
            row = conn.execute(
                text(f"SELECT * FROM {CONFIG_TABLE} WHERE name = :name LIMIT 1"),
                {"name": config_name},
            ).first()
        ctx.event("info", "asr", "ASR provider config saved", {"provider": payload.provider, "model": payload.model, "has_api_key": ctx.secret_store.has(api_key_ref)})
        return {"ok": True, "config": row_to_public(ctx, row), "models": model_options()}

    @router.post("/test")
    def test_config(payload: ASRConnectionTestPayload) -> dict[str, Any]:
        provider = payload.provider.strip()
        model = payload.model.strip()
        selected = next((item for item in model_options() if item["provider"] == provider and item["model"] == model), None)
        if selected is None:
            return connection_result(False, "Unknown ASR model", f"{provider}/{model}")
        api_key = load_stored_key(ctx, provider)
        resolution = resolve_endpoint(provider, model, "asr", f"{provider}_key")
        started_at = int(time.time() * 1000)
        result = test_asr_connection(provider, model, api_key)
        finished_at = int(time.time() * 1000)
        ctx.local_usage.record(
            provider=provider,
            model_id=model,
            modality="asr",
            proxy_policy=resolution.proxy_policy,
            status="ok" if result["ok"] else "failed",
            error_code="" if result["ok"] else str(result.get("message") or "connection_failed"),
            started_at=started_at,
            finished_at=finished_at,
        )
        ctx.event("info" if result["ok"] else "warn", "asr", "ASR connection test", {"provider": provider, "model": model, "status": result["status"]})
        return result

    return router
