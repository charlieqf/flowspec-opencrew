from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any


DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
USD_MICROS = 1_000_000
XAI_USD_TICKS_PER_DOLLAR = 10_000_000_000


SECRET_REPLACEMENTS = (
    (re.compile(r"(postgresql(?:\+\w+)?://[^:\s/@]+:)[^@\s]+(@)", re.I), r"\1***\2"),
    (re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://[^:\s/@]+:)[^@\s]+(@)", re.I), r"\1***\2"),
    (re.compile(r"(password\s*[:=]\s*[\"']?)[^\"',}\s]+", re.I), r"\1***"),
    (re.compile(r"([?&]key=)[^&\s\"'}]+", re.I), r"\1***"),
    (re.compile(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", re.I), r"\1***"),
    (re.compile(r"(Authorization[\"']?\s*[:=]\s*[\"']?\s*Bearer\s+)[^\"',}\s]+", re.I), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", re.I), r"\1***"),
    (re.compile(r"(x-api-key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", re.I), r"\1***"),
    (re.compile(r"\bsk-[A-Za-z0-9._~+/=-]+", re.I), "[REDACTED_SECRET]"),
    (re.compile(r"\bmihomo://[^\s\"']+", re.I), "[REDACTED_PROXY_URL]"),
)


def now_ms() -> int:
    return int(time.time() * 1000)


def text_value(value: Any) -> str:
    return str(value or "").strip()


def env_text(name: str) -> str:
    return os.environ.get(name, "").strip()


def first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            text = text_value(value)
            if text:
                return value
    return ""


def redact_secret_text(value: str) -> str:
    text = str(value or "").replace("\\/", "/")
    for pattern, replacement in SECRET_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items() if str(key).lower() not in {"api_key", "apikey", "api_key_ciphertext"}}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def workspace_from_path(path: Path) -> Path:
    current = path.resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / "SessionContext" / "Variables.json").exists() or (candidate / "0_SessionContext" / "Variables.json").exists():
            return candidate
    return path.resolve().parent


def variables_for_workspace(workspace: Path) -> dict[str, Any]:
    for rel in ("SessionContext/Variables.json", "0_SessionContext/Variables.json"):
        path = workspace / rel
        if path.exists():
            return read_json(path)
    return {}


def database_url_for_workspace(workspace: Path) -> str:
    variables = variables_for_workspace(workspace)
    value = text_value(variables.get("database_url")) or os.environ.get(DATABASE_URL_ENV, "").strip()
    return value or DEFAULT_DATABASE_URL


def request_id_for_call(*, tool_name: str, asset_key: str, kind: str, provider: str, model_id: str, request: dict[str, Any]) -> str:
    seed = json.dumps(
        {
            "tool_name": tool_name,
            "asset_key": asset_key,
            "kind": kind,
            "provider": provider,
            "model_id": model_id,
            "request": json_safe(request),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"req_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}"


def step_id_from_tool_name(tool_name: str) -> str:
    match = re.match(r"^(\d{2}(?:_\d{2})?)_", text_value(tool_name))
    return match.group(1) if match else ""


def units_from_response(modality: str, response: dict[str, Any] | None, request: dict[str, Any]) -> dict[str, Any]:
    response = response or {}
    usage = response.get("usage") or response.get("usageMetadata") or response.get("usage_metadata")
    if isinstance(usage, dict) and usage:
        return dict(usage)
    units: dict[str, Any] = {"request": 1}
    modality_key = modality.lower()
    if modality_key in {"tts", "audio", "asr", "lipsync"}:
        duration = response.get("duration") or response.get("audio_seconds") or response.get("duration_seconds")
        if duration:
            units["audio_second" if modality_key != "lipsync" else "video_second"] = duration
    elif modality_key == "image":
        units["image"] = 1
        if request.get("size"):
            units["image_size"] = request.get("size")
    elif modality_key == "video":
        duration = response.get("duration") or response.get("requested_duration") or response.get("duration_seconds") or request.get("requested_duration_seconds")
        if duration:
            units["video_second"] = duration
    return units


def cost_usd_to_micros(value: Any) -> int | None:
    try:
        return round(float(value) * USD_MICROS)
    except (TypeError, ValueError):
        return None


def cost_ticks_to_micros(value: Any) -> int | None:
    try:
        return round(int(value) * USD_MICROS / XAI_USD_TICKS_PER_DOLLAR)
    except (TypeError, ValueError):
        return None


def actual_cost_from_response(response: dict[str, Any] | None) -> tuple[int | None, str, dict[str, Any]]:
    response = response or {}
    usage = response.get("usage") or response.get("usageMetadata") or response.get("usage_metadata")
    usage_dict = usage if isinstance(usage, dict) else {}

    for source, container in (
        ("response.usage.cost_micros", usage_dict),
        ("response.cost_micros", response),
    ):
        if "cost_micros" not in container:
            continue
        try:
            return int(container.get("cost_micros")), source, {"cost_micros": container.get("cost_micros")}
        except (TypeError, ValueError):
            continue

    if "cost_in_usd_ticks" in usage_dict:
        cost_micros = cost_ticks_to_micros(usage_dict.get("cost_in_usd_ticks"))
        if cost_micros is not None:
            return cost_micros, "response.usage.cost_in_usd_ticks", {"usage": usage_dict}

    for source, container, keys in (
        ("response.usage.cost_usd", usage_dict, ("cost_usd", "cost_in_usd", "cost")),
        ("response.cost_usd", response, ("cost_usd", "cost_in_usd", "cost")),
    ):
        for key in keys:
            if key not in container:
                continue
            cost_micros = cost_usd_to_micros(container.get(key))
            if cost_micros is not None:
                return cost_micros, source.replace("cost_usd", key), {key: container.get(key)}

    return None, "", {}


def local_usage_record_result(
    *,
    database_url: str,
    request_id: str,
    provider: str,
    model_id: str,
    modality: str,
    status: str,
    units: dict[str, Any],
    task_id: str | int | None = None,
    attempt_id: str | int | None = None,
    step_id: str | None = None,
    idempotency_key: str | None = None,
    actual_cost_micros: int | None = None,
    actual_cost_source: str = "",
    actual_cost_raw: dict[str, Any] | None = None,
    error_code: str = "",
    started_at: int | None = None,
    finished_at: int | None = None,
) -> dict[str, Any]:
    if os.environ.get("OPENCREW_DISABLE_LOCAL_USAGE_LOG", "").strip() == "1":
        return {"local_usage_id": "", "status": "disabled", "recorded": False, "error": "", "error_type": ""}
    normalized = normalize_database_url(database_url)
    created_at = now_ms()
    started = started_at or created_at
    finished = finished_at or created_at
    sql = """
INSERT INTO local_usage_log (
  request_id, task_id, attempt_id, step_id, idempotency_key,
  provider, model_id, modality, provider_mode, billing_mode,
  proxy_policy, status, units_json, est_cost_micros,
  actual_cost_micros, actual_cost_currency, actual_cost_source, actual_cost_raw_json,
  pricebook_version, billing_reconciled_at, error_code,
  started_at, finished_at, created_at
) VALUES (
  %s, %s, %s, %s, %s,
  %s, %s, %s, 'local_box', 'local_usage_only',
  '', %s, CAST(%s AS JSONB), NULL,
  %s, %s, %s, CAST(%s AS JSONB),
  %s, NULL, %s,
  %s, %s, %s
)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING id
"""
    payload = json.dumps(units or {}, ensure_ascii=True)
    actual_cost_payload = json.dumps(json_safe(actual_cost_raw or {}), ensure_ascii=True)
    params = (
        request_id,
        str(task_id) if task_id not in (None, "") else None,
        str(attempt_id) if attempt_id not in (None, "") else None,
        step_id or None,
        idempotency_key or None,
        provider,
        model_id,
        modality,
        status,
        payload,
        actual_cost_micros,
        "USD" if actual_cost_source else "",
        actual_cost_source,
        actual_cost_payload,
        "provider_response" if actual_cost_source else "",
        error_code,
        started,
        finished,
        created_at,
    )
    try:
        import psycopg  # type: ignore

        with psycopg.connect(normalized, connect_timeout=3) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone()
        return {"local_usage_id": str(row[0]) if row else "", "status": "recorded" if row else "duplicate", "recorded": bool(row), "error": "", "error_type": ""}
    except ImportError:
        try:
            import psycopg2  # type: ignore

            conn = psycopg2.connect(normalized, connect_timeout=3)
            try:
                with conn.cursor() as cursor:
                    cursor.execute(sql, params)
                    row = cursor.fetchone()
                conn.commit()
            finally:
                conn.close()
            return {"local_usage_id": str(row[0]) if row else "", "status": "recorded" if row else "duplicate", "recorded": bool(row), "error": "", "error_type": ""}
        except Exception as exc:
            return {
                "local_usage_id": "",
                "status": "failed",
                "recorded": False,
                "error": redact_secret_text(f"{type(exc).__name__}: {exc}")[:1000],
                "error_type": type(exc).__name__,
            }
    except Exception as exc:
        return {
            "local_usage_id": "",
            "status": "failed",
            "recorded": False,
            "error": redact_secret_text(f"{type(exc).__name__}: {exc}")[:1000],
            "error_type": type(exc).__name__,
        }


def warn_local_usage_failure(request_id: str, result: dict[str, Any]) -> None:
    if str(result.get("status") or "") != "failed":
        return
    message = redact_secret_text(str(result.get("error") or "unknown local usage persistence error"))
    sys.stderr.write(f"[provider_audit] local usage log failed for {request_id}: {message}\n")


def record_local_usage(
    *,
    database_url: str,
    request_id: str,
    provider: str,
    model_id: str,
    modality: str,
    status: str,
    units: dict[str, Any],
    task_id: str | int | None = None,
    attempt_id: str | int | None = None,
    step_id: str | None = None,
    idempotency_key: str | None = None,
    actual_cost_micros: int | None = None,
    actual_cost_source: str = "",
    actual_cost_raw: dict[str, Any] | None = None,
    error_code: str = "",
    started_at: int | None = None,
    finished_at: int | None = None,
) -> str:
    result = local_usage_record_result(
        database_url=database_url,
        request_id=request_id,
        provider=provider,
        model_id=model_id,
        modality=modality,
        status=status,
        units=units,
        task_id=task_id,
        attempt_id=attempt_id,
        step_id=step_id,
        idempotency_key=idempotency_key,
        actual_cost_micros=actual_cost_micros,
        actual_cost_source=actual_cost_source,
        actual_cost_raw=actual_cost_raw,
        error_code=error_code,
        started_at=started_at,
        finished_at=finished_at,
    )
    warn_local_usage_failure(request_id, result)
    return text_value(result.get("local_usage_id"))


def record_model_call_audit(
    *,
    workspace: Path,
    tool_dir_name: str,
    tool_name: str,
    step_index: int,
    asset_key: str,
    kind: str,
    provider: str,
    model_id: str,
    request: dict[str, Any],
    response: dict[str, Any] | None = None,
    status: str = "ok",
    error_code: str = "",
    prompt_path: str = "",
    output_summary: str = "",
    started_at: int | None = None,
    finished_at: int | None = None,
    task_id: str | int | None = None,
    attempt_id: str | int | None = None,
    step_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    variables = variables_for_workspace(workspace)
    modality = kind.lower()
    request_id = request_id_for_call(tool_name=tool_name, asset_key=asset_key, kind=kind, provider=provider, model_id=model_id, request=request)
    units = units_from_response(modality, response, request)
    actual_cost_micros, actual_cost_source, actual_cost_raw = actual_cost_from_response(response)
    task_id_value = first_value(task_id, env_text("OPENCREW_TASK_ID"), variables.get("task_id"))
    attempt_id_value = first_value(attempt_id, env_text("OPENCREW_ATTEMPT_ID"), variables.get("current_attempt_id"), variables.get("attempt_id"))
    step_id_value = text_value(first_value(step_id, env_text("OPENCREW_STEP_ID"), step_id_from_tool_name(tool_name)))
    effective_idempotency_key = text_value(idempotency_key)
    if not effective_idempotency_key:
        if text_value(task_id_value) and text_value(attempt_id_value) and step_id_value:
            effective_idempotency_key = f"model:{text_value(task_id_value)}:{text_value(attempt_id_value)}:{step_id_value}:{request_id}"
        else:
            effective_idempotency_key = request_id
    local_usage_result = local_usage_record_result(
        database_url=database_url_for_workspace(workspace),
        request_id=request_id,
        provider=provider,
        model_id=model_id,
        modality=modality,
        status=status,
        units=units,
        task_id=task_id_value,
        attempt_id=attempt_id_value,
        step_id=step_id_value,
        idempotency_key=effective_idempotency_key,
        actual_cost_micros=actual_cost_micros,
        actual_cost_source=actual_cost_source,
        actual_cost_raw=actual_cost_raw,
        error_code=error_code,
        started_at=started_at,
        finished_at=finished_at,
    )
    warn_local_usage_failure(request_id, local_usage_result)
    local_usage_id = text_value(local_usage_result.get("local_usage_id"))
    summary = output_summary or text_value((response or {}).get("summary") or (response or {}).get("status") or (response or {}).get("output_path"))
    audit = {
        "schema_version": "1.0",
        "tool_use_session_id": text_value(variables.get("tool_use_session_id")),
        "step_index": step_index,
        "step_id": step_id_value,
        "tool_name": tool_name,
        "task_id": task_id_value,
        "opencrew_session_id": first_value(env_text("OPENCREW_SESSION_ID"), variables.get("opencrew_session_id")),
        "opencode_session_id": text_value(variables.get("opencode_session_id")),
        "attempt_id": attempt_id_value,
        "model_provider": provider,
        "model_id": model_id,
        "system_prompt_path": "",
        "user_prompt_path": prompt_path,
        "input_files": [str(item) for item in request.get("reference_paths") or request.get("input_files") or []],
        "output_summary": redact_secret_text(summary),
        "error_summary": redact_secret_text(error_code),
        "request_id": request_id,
        "local_usage_id": local_usage_id,
        "local_usage_status": text_value(local_usage_result.get("status")) or "unknown",
        "local_usage_recorded": bool(local_usage_result.get("recorded")),
        "local_usage_error": redact_secret_text(text_value(local_usage_result.get("error"))),
        "local_usage_error_type": text_value(local_usage_result.get("error_type")),
        "gateway_request_id": "",
        "provider_mode": "local_box",
        "billing_mode": "local_usage_only",
        "usage_summary": units,
        "actual_cost_micros": actual_cost_micros,
        "actual_cost_currency": "USD" if actual_cost_source else "",
        "actual_cost_source": actual_cost_source,
        "sensitivity": "normal",
        "redaction_status": "redacted",
        "idempotency_key": effective_idempotency_key,
    }
    safe_asset = re.sub(r"[^A-Za-z0-9_.-]+", "_", asset_key or "asset").strip("_") or "asset"
    safe_kind = re.sub(r"[^A-Za-z0-9_.-]+", "_", kind or "model").strip("_") or "model"
    path = workspace / tool_dir_name / "Report" / "ModelCalls" / f"ModelCallAudit_{safe_asset}_{safe_kind}_{request_id}.json"
    write_json(path, audit)
    return audit


def record_model_call_from_prompt_dir(
    *,
    prompt_dir: Path,
    tool_dir_name: str,
    tool_name: str,
    step_index: int,
    asset_key: str,
    kind: str,
    request: dict[str, Any],
    response: dict[str, Any] | None = None,
    status: str = "ok",
    error_code: str = "",
) -> dict[str, Any]:
    workspace = workspace_from_path(prompt_dir)
    provider_config = request.get("provider_config") if isinstance(request.get("provider_config"), dict) else {}
    provider = text_value(provider_config.get("provider") or (response or {}).get("provider") or request.get("provider"))
    model_id = text_value(provider_config.get("model") or (response or {}).get("model") or request.get("model"))
    prompt_path = text_value(request.get("prompt_path"))
    return record_model_call_audit(
        workspace=workspace,
        tool_dir_name=tool_dir_name,
        tool_name=tool_name,
        step_index=step_index,
        asset_key=asset_key,
        kind=kind,
        provider=provider or "unknown",
        model_id=model_id or "unknown",
        request=request,
        response=response,
        status=status,
        error_code=error_code,
        prompt_path=prompt_path,
    )
