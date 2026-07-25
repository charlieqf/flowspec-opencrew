from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


USD_MICROS = 1_000_000
PRICEBOOK_VERSION = "local_estimate_v3"


USAGE_SELECT_COLUMNS = """
id, request_id, provider, model_id, modality, provider_mode, billing_mode,
task_id, attempt_id, step_id, idempotency_key,
proxy_policy, status, units_json, est_cost_micros,
actual_cost_micros, actual_cost_currency, actual_cost_source, actual_cost_raw_json,
pricebook_version, billing_reconciled_at, error_code,
started_at, finished_at, created_at
"""


@dataclass(frozen=True)
class UnitPrice:
    unit_key: str
    cost_micros: int
    sell_micros: int
    label: str
    sell_cap_micros: int = 0


DEFAULT_PRICEBOOK: dict[str, UnitPrice] = {
    "input_token": UnitPrice("input_token", 2, 5, "input token"),
    "output_token": UnitPrice("output_token", 8, 20, "output token"),
    "character": UnitPrice("character", 4, 12, "character"),
    "input_character": UnitPrice("input_character", 0, 0, "input character"),
    "output_character": UnitPrice("output_character", 0, 0, "output character"),
    "prompt_character": UnitPrice("prompt_character", 0, 0, "prompt character"),
    "audio_second_observed": UnitPrice("audio_second_observed", 0, 0, "observed audio second"),
    "reference": UnitPrice("reference", 0, 0, "reference asset"),
    "image": UnitPrice("image", int(0.05 * USD_MICROS), int(0.10 * USD_MICROS), "image"),
    "video_second": UnitPrice("video_second", int(0.12 * USD_MICROS), int(0.24 * USD_MICROS), "video second"),
    "audio_second": UnitPrice("audio_second", int(0.002 * USD_MICROS), int(0.006 * USD_MICROS), "audio second"),
    "request": UnitPrice("request", 0, 0, "request"),
    "artifact_json_kb": UnitPrice("artifact_json_kb", 0, 200, "artifact JSON KB", int(0.05 * USD_MICROS)),
    "artifact_image_kb": UnitPrice("artifact_image_kb", 0, 5, "artifact image KB", int(0.02 * USD_MICROS)),
    "artifact_wav_kb": UnitPrice("artifact_wav_kb", 0, 2, "artifact WAV KB", int(0.05 * USD_MICROS)),
}

MODEL_PRICEBOOK: dict[tuple[str, str, str], dict[str, UnitPrice]] = {
    ("openai", "gpt-5.5", "chat"): {
        "input_token": UnitPrice("input_token", 2, 5, "input token"),
        "output_token": UnitPrice("output_token", 8, 20, "output token"),
    },
    ("openai", "gpt-5.5", "text"): {
        "input_token": UnitPrice("input_token", 2, 5, "input token"),
        "output_token": UnitPrice("output_token", 8, 20, "output token"),
    },
    ("gemini", "gemini-3.1-flash-image", "image"): {"image": UnitPrice("image", int(0.067 * USD_MICROS), int(0.14 * USD_MICROS), "image")},
    ("google", "gemini-3.1-flash-image", "image"): {"image": UnitPrice("image", int(0.067 * USD_MICROS), int(0.14 * USD_MICROS), "image")},
    ("gemini", "gemini-2.5-flash-image", "image"): {"image": UnitPrice("image", int(0.039 * USD_MICROS), int(0.08 * USD_MICROS), "image")},
    ("google", "gemini-2.5-flash-image", "image"): {"image": UnitPrice("image", int(0.039 * USD_MICROS), int(0.08 * USD_MICROS), "image")},
    ("openai", "gpt-image-1", "image"): {"image": UnitPrice("image", int(0.08 * USD_MICROS), int(0.16 * USD_MICROS), "image")},
    ("openai", "gpt-image-1.5", "image"): {"image": UnitPrice("image", int(0.08 * USD_MICROS), int(0.16 * USD_MICROS), "image")},
    ("openai", "gpt-image-2", "image"): {"image": UnitPrice("image", int(0.08 * USD_MICROS), int(0.16 * USD_MICROS), "image")},
    ("xai", "grok-imagine-image", "image"): {"image": UnitPrice("image", int(0.02 * USD_MICROS), int(0.05 * USD_MICROS), "image")},
    ("xai", "grok-imagine-image-quality", "image"): {"image": UnitPrice("image", int(0.05 * USD_MICROS), int(0.10 * USD_MICROS), "image")},
    ("openai", "sora-2", "video"): {"video_second": UnitPrice("video_second", int(0.10 * USD_MICROS), int(0.22 * USD_MICROS), "video second")},
    ("openai", "sora-2-pro", "video"): {"video_second": UnitPrice("video_second", int(0.30 * USD_MICROS), int(0.66 * USD_MICROS), "video second")},
    ("xai", "grok-imagine-video", "video"): {"video_second": UnitPrice("video_second", int(0.05 * USD_MICROS), int(0.12 * USD_MICROS), "video second")},
    ("xai", "grok-imagine-video-1.5-preview", "video"): {
        "video_second": UnitPrice("video_second", int(0.14 * USD_MICROS), int(0.30 * USD_MICROS), "720p video second"),
        "video_480p_second": UnitPrice("video_480p_second", int(0.08 * USD_MICROS), int(0.18 * USD_MICROS), "480p video second"),
        "video_720p_second": UnitPrice("video_720p_second", int(0.14 * USD_MICROS), int(0.30 * USD_MICROS), "720p video second"),
        "video_1080p_second": UnitPrice("video_1080p_second", int(0.25 * USD_MICROS), int(0.52 * USD_MICROS), "1080p video second"),
        "reference": UnitPrice("reference", int(0.01 * USD_MICROS), int(0.02 * USD_MICROS), "input image"),
    },
    ("openrouter", "bytedance/seedance-2.0", "video"): {"video_second": UnitPrice("video_second", int(0.08 * USD_MICROS), int(0.18 * USD_MICROS), "video second")},
    ("openrouter", "bytedance/seedance-2.0-fast", "video"): {"video_second": UnitPrice("video_second", int(0.07 * USD_MICROS), int(0.16 * USD_MICROS), "video second")},
    ("openrouter", "bytedance/seedance-1-5-pro", "video"): {"video_second": UnitPrice("video_second", int(0.08 * USD_MICROS), int(0.18 * USD_MICROS), "video second")},
    ("bytedance", "seedance-2.0", "video"): {"video_second": UnitPrice("video_second", int(0.08 * USD_MICROS), int(0.18 * USD_MICROS), "video second")},
    ("bytedance", "seedance-2.0-fast", "video"): {"video_second": UnitPrice("video_second", int(0.07 * USD_MICROS), int(0.16 * USD_MICROS), "video second")},
    ("gemini", "veo-3.1-fast-generate-preview", "video"): {"video_second": UnitPrice("video_second", int(0.10 * USD_MICROS), int(0.22 * USD_MICROS), "video second")},
    ("google", "veo-3.1-fast-generate-preview", "video"): {"video_second": UnitPrice("video_second", int(0.10 * USD_MICROS), int(0.22 * USD_MICROS), "video second")},
    ("kling", "kling-3.0-turbo", "video"): {"video_second": UnitPrice("video_second", int(0.14 * USD_MICROS), int(0.30 * USD_MICROS), "video second")},
    ("kling", "kling-v3-omni", "video"): {"video_second": UnitPrice("video_second", int(0.112 * USD_MICROS), int(0.24 * USD_MICROS), "video second")},
    ("wan", "wan2.7-i2v-2026-04-25", "video"): {"video_second": UnitPrice("video_second", int(0.085 * USD_MICROS), int(0.18 * USD_MICROS), "video second")},
    ("sync", "lipsync-2", "lipsync"): {"video_second": UnitPrice("video_second", int(0.08 * USD_MICROS), int(0.18 * USD_MICROS), "video second")},
    ("sync.so", "lipsync-2", "lipsync"): {"video_second": UnitPrice("video_second", int(0.08 * USD_MICROS), int(0.18 * USD_MICROS), "video second")},
    ("syncso", "sync.so", "lipsync"): {"video_second": UnitPrice("video_second", int(0.08 * USD_MICROS), int(0.18 * USD_MICROS), "video second")},
    ("kling", "kling-lipsync-advanced", "lipsync"): {"video_second": UnitPrice("video_second", int((0.1 / 7.1) * USD_MICROS), int((0.2 / 7.1) * USD_MICROS), "video second")},
    ("heygen", "heygen-lipsync-precision", "lipsync"): {"request": UnitPrice("request", int(0.08 * USD_MICROS), int(0.18 * USD_MICROS), "request")},
    ("google", "gemini-3.1-flash-tts-preview", "tts"): {
        "input_token": UnitPrice("input_token", 2, 5, "input token"),
        "output_token": UnitPrice("output_token", 8, 20, "output token"),
        "character": UnitPrice("character", 4, 12, "character"),
    },
    ("qwen", "qwen3-tts-flash", "tts"): {"character": UnitPrice("character", 3, 8, "character")},
    ("qwen", "qwen3-tts-instruct-flash", "tts"): {"character": UnitPrice("character", 3, 8, "character")},
    ("bytedance", "seed-tts-1.1", "tts"): {"character": UnitPrice("character", 2, 6, "character")},
    ("heygen", "heygen-voice-clone-v3", "tts"): {"character": UnitPrice("character", 10, 30, "character")},
    ("heygen", "heygen-voice-clone-v3", "voice_clone"): {"request": UnitPrice("request", int(0.20 * USD_MICROS), int(0.40 * USD_MICROS), "request")},
    ("heygen", "avatar iv", "digital_human"): {"video_second": UnitPrice("video_second", int(0.12 * USD_MICROS), int(0.24 * USD_MICROS), "video second")},
    ("heygen", "avatar v", "digital_human"): {"video_second": UnitPrice("video_second", int(0.12 * USD_MICROS), int(0.24 * USD_MICROS), "video second")},
    ("heygen", "heygen video agent", "digital_human"): {"video_second": UnitPrice("video_second", int(0.12 * USD_MICROS), int(0.24 * USD_MICROS), "video second")},
}


def now_ms() -> int:
    return int(time.time() * 1000)


def normalize_key(value: Any) -> str:
    return str(value or "").strip().lower()


def parse_units(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def numeric(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def unit_price(provider: str, model_id: str, modality: str, unit_key: str) -> tuple[UnitPrice, str]:
    provider_key = normalize_key(provider)
    model_key = normalize_key(model_id)
    modality_key = normalize_key(modality)
    specific = MODEL_PRICEBOOK.get((provider_key, model_key, modality_key), {})
    if unit_key in specific:
        return specific[unit_key], "model"
    return DEFAULT_PRICEBOOK.get(unit_key, UnitPrice(unit_key, 0, 0, unit_key)), "default"


def pricebook_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for unit_key, price in sorted(DEFAULT_PRICEBOOK.items()):
        rows.append(
            {
                "provider": "*",
                "model_id": "*",
                "modality": "*",
                "unit_key": unit_key,
                "unit_label": price.label,
                "provider_unit_cost_micros": price.cost_micros,
                "customer_unit_price_micros": price.sell_micros,
                "customer_unit_cap_micros": price.sell_cap_micros,
                "source": "default",
            }
        )
    for (provider, model_id, modality), prices in sorted(MODEL_PRICEBOOK.items()):
        for unit_key, price in sorted(prices.items()):
            rows.append(
                {
                    "provider": provider,
                    "model_id": model_id,
                    "modality": modality,
                    "unit_key": unit_key,
                    "unit_label": price.label,
                    "provider_unit_cost_micros": price.cost_micros,
                    "customer_unit_price_micros": price.sell_micros,
                    "customer_unit_cap_micros": price.sell_cap_micros,
                    "source": "model",
                }
            )
    return rows


def capped_artifact_kb(unit_key: str, byte_size: int) -> tuple[float, float]:
    # Artifact caps currently use the default artifact pricebook; keep this in sync if model-specific artifact prices are added.
    raw_kb = max(0.0, float(byte_size or 0) / 1024.0)
    price = DEFAULT_PRICEBOOK.get(unit_key)
    if price is None or price.sell_micros <= 0 or price.sell_cap_micros <= 0:
        return raw_kb, raw_kb
    capped_kb = min(raw_kb, price.sell_cap_micros / price.sell_micros)
    return raw_kb, capped_kb


def billable_units(units: dict[str, Any], modality: str) -> dict[str, float]:
    result: dict[str, float] = {}
    ignored_keys = {
        "cost",
        "cost_in_usd",
        "cost_in_usd_ticks",
        "cost_micros",
        "cost_usd",
        "totalTokenCount",
        "totaltokencount",
        "total_tokens",
        "totalTokens",
        "totaltokens",
    }
    aliases = {
        "input_tokens": "input_token",
        "inputTokens": "input_token",
        "promptTokenCount": "input_token",
        "prompt_tokens": "input_token",
        "approx_input_tokens": "input_token",
        "estimated_input_tokens": "input_token",
        "output_tokens": "output_token",
        "outputTokens": "output_token",
        "candidatesTokenCount": "output_token",
        "completion_tokens": "output_token",
        "approx_output_tokens": "output_token",
        "estimated_output_tokens": "output_token",
        "characters": "character",
        "char_count": "character",
        "chars": "character",
        "text_characters": "character",
        "text_chars": "character",
        "input_characters": "input_character",
        "input_chars": "input_character",
        "output_characters": "output_character",
        "output_chars": "output_character",
        "prompt_characters": "prompt_character",
        "prompt_chars": "prompt_character",
        "audio_seconds": "audio_second",
        "audio_second": "audio_second",
        "audio_seconds_observed": "audio_second_observed",
        "observed_audio_seconds": "audio_second_observed",
        "raw_audio_seconds": "audio_second_observed",
        "video_seconds": "video_second",
        "video_second": "video_second",
        "duration_seconds": "video_second" if normalize_key(modality) in {"video", "lipsync", "digital_human"} else "audio_second",
        "reference_count": "reference",
        "references": "reference",
        "images": "image",
        "image": "image",
        "request": "request",
    }
    for raw_key, raw_value in units.items():
        raw_key_text = str(raw_key)
        if raw_key_text in ignored_keys or raw_key_text.lower() in ignored_keys:
            continue
        key = aliases.get(raw_key_text, raw_key_text)
        amount = numeric(raw_value)
        if amount <= 0:
            continue
        result[key] = result.get(key, 0.0) + amount
    return result


def build_unit_lines(row: dict[str, Any], units: dict[str, float]) -> tuple[list[dict[str, Any]], int, int, set[str]]:
    unit_lines: list[dict[str, Any]] = []
    estimated_cost_micros = 0
    charge_micros = 0
    price_sources: set[str] = set()
    for unit_key, amount in units.items():
        price, source = unit_price(str(row.get("provider") or ""), str(row.get("model_id") or ""), str(row.get("modality") or ""), unit_key)
        line_cost = round(amount * price.cost_micros)
        line_charge = round(amount * price.sell_micros)
        estimated_cost_micros += line_cost
        charge_micros += line_charge
        price_sources.add(source)
        unit_lines.append(
            {
                "unit_key": unit_key,
                "unit_label": price.label,
                "amount": amount,
                "provider_unit_cost_micros": price.cost_micros,
                "customer_unit_price_micros": price.sell_micros,
                "estimated_cost_micros": line_cost,
                "charge_micros": line_charge,
                "cost_micros": line_cost,
                "sell_micros": line_charge,
                "price_source": source,
            }
        )
    return unit_lines, estimated_cost_micros, charge_micros, price_sources


def actual_cost_available(row: dict[str, Any]) -> bool:
    return row.get("actual_cost_micros") is not None or bool(str(row.get("actual_cost_source") or "").strip())


def add_price_lines(target: dict[str, Any], row: dict[str, Any]) -> None:
    bucket = target.setdefault("_price_lines", {})
    for line in row.get("unit_lines") or []:
        key = "|".join(
            [
                str(line.get("unit_key") or ""),
                str(line.get("provider_unit_cost_micros") or 0),
                str(line.get("customer_unit_price_micros") or 0),
                str(line.get("price_source") or ""),
            ]
        )
        item = bucket.setdefault(
            key,
            {
                "unit_key": line.get("unit_key") or "",
                "unit_label": line.get("unit_label") or "",
                "provider_unit_cost_micros": int(line.get("provider_unit_cost_micros") or 0),
                "customer_unit_price_micros": int(line.get("customer_unit_price_micros") or 0),
                "price_source": line.get("price_source") or "",
                "amount": 0.0,
                "estimated_cost_micros": 0,
                "charge_micros": 0,
            },
        )
        item["amount"] = float(item.get("amount") or 0.0) + float(line.get("amount") or 0.0)
        item["estimated_cost_micros"] = int(item.get("estimated_cost_micros") or 0) + int(line.get("estimated_cost_micros") or 0)
        item["charge_micros"] = int(item.get("charge_micros") or 0) + int(line.get("charge_micros") or 0)


def finalize_price_lines(item: dict[str, Any]) -> None:
    bucket = item.pop("_price_lines", {})
    item["price_lines"] = sorted(bucket.values(), key=lambda line: (str(line.get("unit_key") or ""), int(line.get("customer_unit_price_micros") or 0)))


def enrich_usage_row(row: dict[str, Any]) -> dict[str, Any]:
    raw_units = parse_units(row.get("units_json"))
    units = billable_units(raw_units, str(row.get("modality") or ""))
    unit_lines, pricebook_estimated_cost_micros, pricebook_charge_micros, price_sources = build_unit_lines(row, units)
    recorded_estimated_cost_micros = int(row.get("est_cost_micros") or 0)
    estimated_cost_micros = recorded_estimated_cost_micros if recorded_estimated_cost_micros > 0 else pricebook_estimated_cost_micros
    charge_micros = pricebook_charge_micros
    charge_source = "pricebook" if charge_micros > 0 else "none"
    if charge_micros <= 0 and estimated_cost_micros > 0:
        charge_micros = round(estimated_cost_micros * 2)
        charge_source = "estimated_cost_markup_fallback"
        price_sources.add("recorded_estimate_markup")

    has_actual_cost = actual_cost_available(row)
    actual_cost_micros = int(row.get("actual_cost_micros") or 0) if has_actual_cost else 0
    actual_cost_source = str(row.get("actual_cost_source") or "").strip()
    if charge_micros <= 0 and has_actual_cost:
        charge_micros = round(actual_cost_micros * 2)
        charge_source = "actual_cost_markup_fallback"
        price_sources.add("actual_cost_markup")

    provider_cost_micros = actual_cost_micros if has_actual_cost else estimated_cost_micros
    cost_basis = "actual" if has_actual_cost else "estimated" if estimated_cost_micros > 0 else "none"
    profit_micros = charge_micros - provider_cost_micros
    estimate_source = "recorded_estimate" if recorded_estimated_cost_micros > 0 else "pricebook_estimate" if pricebook_estimated_cost_micros > 0 else "none"
    return {
        **row,
        "units": units,
        "raw_units": raw_units,
        "actual_cost_raw": parse_json_dict(row.get("actual_cost_raw_json")),
        "unit_lines": unit_lines,
        "has_actual_cost": has_actual_cost,
        "actual_cost_micros": actual_cost_micros,
        "actual_cost_currency": row.get("actual_cost_currency") or ("USD" if has_actual_cost else ""),
        "actual_cost_source": actual_cost_source,
        "estimated_cost_micros": estimated_cost_micros,
        "estimate_source": estimate_source,
        "provider_cost_micros": provider_cost_micros,
        "charge_micros": charge_micros,
        "charge_source": charge_source,
        "cost_micros": provider_cost_micros,
        "sell_micros": charge_micros,
        "profit_micros": profit_micros,
        "cost_basis": cost_basis,
        "price_source": ",".join(sorted(price_sources)) or "none",
        "pricebook_version": row.get("pricebook_version") or PRICEBOOK_VERSION,
    }


def add_amount(target: dict[str, Any], row: dict[str, Any]) -> None:
    target["request_count"] = int(target.get("request_count") or 0) + 1
    for key in (
        "provider_cost_micros",
        "cost_micros",
        "estimated_cost_micros",
        "charge_micros",
        "sell_micros",
        "profit_micros",
    ):
        target[key] = int(target.get(key) or 0) + int(row.get(key) or 0)
    if row.get("has_actual_cost"):
        target["actual_cost_micros"] = int(target.get("actual_cost_micros") or 0) + int(row.get("actual_cost_micros") or 0)
        target["actual_cost_count"] = int(target.get("actual_cost_count") or 0) + 1
    elif int(row.get("estimated_cost_micros") or 0) > 0:
        target["estimated_only_cost_micros"] = int(target.get("estimated_only_cost_micros") or 0) + int(row.get("estimated_cost_micros") or 0)
        target["estimated_cost_count"] = int(target.get("estimated_cost_count") or 0) + 1
    else:
        target["unpriced_count"] = int(target.get("unpriced_count") or 0) + 1
    basis_counts = target.setdefault("cost_basis_counts", {})
    cost_basis = str(row.get("cost_basis") or "none")
    basis_counts[cost_basis] = int(basis_counts.get(cost_basis) or 0) + 1
    for unit_key, amount in (row.get("units") or {}).items():
        target_units = target.setdefault("units", {})
        target_units[unit_key] = float(target_units.get(unit_key) or 0.0) + float(amount)
    add_price_lines(target, row)


def group_rows(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in fields)
        item = grouped.setdefault(key, {field: row.get(field) or "" for field in fields})
        add_amount(item, row)
    result = list(grouped.values())
    for item in result:
        finalize_price_lines(item)
    return sorted(result, key=lambda item: int(item.get("sell_micros") or 0), reverse=True)


def empty_totals() -> dict[str, Any]:
    return {
        "request_count": 0,
        "provider_cost_micros": 0,
        "cost_micros": 0,
        "actual_cost_micros": 0,
        "actual_cost_count": 0,
        "estimated_cost_micros": 0,
        "estimated_only_cost_micros": 0,
        "estimated_cost_count": 0,
        "unpriced_count": 0,
        "charge_micros": 0,
        "sell_micros": 0,
        "profit_micros": 0,
        "units": {},
        "cost_basis_counts": {},
    }


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = empty_totals()
    for row in rows:
        add_amount(totals, row)
    finalize_price_lines(totals)
    return totals


class LocalMeteringService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _task_metadata(self, task_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
SELECT
  t.id AS task_id,
  t.session_id,
  t.status AS task_status,
  t.latest_attempt_id,
  t.run_model_provider,
  t.run_model_id,
  t.created_at AS task_created_at,
  t.updated_at AS task_updated_at,
  s.title AS session_title,
  s.status AS session_status,
  s.opencode_session_id,
  s.workspace_dir,
  la.attempt_no AS latest_attempt_no,
  la.status AS latest_attempt_status
FROM openclip_tasks t
LEFT JOIN sessions s ON s.id = t.session_id
LEFT JOIN openclip_attempts la ON la.id = t.latest_attempt_id
WHERE t.id = :task_id
"""
                ),
                {"task_id": task_id},
            ).mappings().first()
        return dict(row) if row else None

    def _task_attempts(self, task_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
SELECT id, task_id, session_id, attempt_no, status, run_model_provider, run_model_id,
       summary, started_at, finished_at, created_at
FROM openclip_attempts
WHERE task_id = :task_id
ORDER BY attempt_no ASC, id ASC
"""
                ),
                {"task_id": task_id},
            ).mappings().all()
        return [dict(row) for row in rows]

    def _task_usage_rows(self, task_id: int, *, attempt_ids: list[int] | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"task_id": str(task_id)}
        where = "task_id = :task_id"
        if attempt_ids is not None:
            if not attempt_ids:
                return []
            placeholders = []
            for index, attempt_id in enumerate(attempt_ids):
                key = f"attempt_id_{index}"
                params[key] = str(attempt_id)
                placeholders.append(f":{key}")
            where += f" AND attempt_id IN ({', '.join(placeholders)})"
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
SELECT {USAGE_SELECT_COLUMNS}
FROM local_usage_log
WHERE {where}
ORDER BY created_at DESC, id DESC
"""
                ),
                params,
            ).mappings().all()
        return [dict(row) for row in rows]

    def _tasks_overview(self, rows: list[dict[str, Any]], *, since: int) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            task_id = str(row.get("task_id") or "").strip()
            if not task_id:
                continue
            item = grouped.setdefault(
                task_id,
                {
                    "task_id": task_id,
                    "first_usage_at": int(row.get("created_at") or 0),
                    "last_usage_at": int(row.get("created_at") or 0),
                },
            )
            created_at = int(row.get("created_at") or 0)
            if created_at:
                item["first_usage_at"] = min(int(item.get("first_usage_at") or created_at), created_at)
                item["last_usage_at"] = max(int(item.get("last_usage_at") or created_at), created_at)
            add_amount(item, row)

        numeric_task_ids = sorted({int(task_id) for task_id in grouped if task_id.isdigit()})
        params: dict[str, Any] = {"since": since}
        id_filter = ""
        if numeric_task_ids:
            placeholders = []
            for index, task_id in enumerate(numeric_task_ids):
                key = f"task_id_{index}"
                params[key] = task_id
                placeholders.append(f":{key}")
            id_filter = f" OR t.id IN ({', '.join(placeholders)})"

        with self.engine.connect() as conn:
            task_rows = conn.execute(
                text(
                    f"""
SELECT
  t.id AS task_id,
  t.session_id,
  t.status AS task_status,
  t.latest_attempt_id,
  t.run_model_provider,
  t.run_model_id,
  t.created_at AS task_created_at,
  t.updated_at AS task_updated_at,
  s.title AS session_title,
  s.status AS session_status,
  la.attempt_no AS latest_attempt_no,
  la.status AS latest_attempt_status
FROM openclip_tasks t
LEFT JOIN sessions s ON s.id = t.session_id
LEFT JOIN openclip_attempts la ON la.id = t.latest_attempt_id
WHERE t.created_at >= :since OR t.updated_at >= :since{id_filter}
"""
                ),
                params,
            ).mappings().all()

        results_by_task: dict[str, dict[str, Any]] = {}
        for task in task_rows:
            task_id = str(task.get("task_id") or "")
            totals = grouped.pop(task_id, None) or empty_totals()
            finalize_price_lines(totals)
            latest_activity_at = max(
                int(task.get("task_updated_at") or 0),
                int(totals.get("last_usage_at") or 0),
            )
            results_by_task[task_id] = {
                **totals,
                "task_id": int(task.get("task_id") or 0),
                "session_id": task.get("session_id"),
                "title": task.get("session_title") or f"Task #{task_id}",
                "task_status": task.get("task_status") or "",
                "session_status": task.get("session_status") or "",
                "latest_attempt_id": task.get("latest_attempt_id"),
                "latest_attempt_no": task.get("latest_attempt_no"),
                "latest_attempt_status": task.get("latest_attempt_status") or "",
                "run_model_provider": task.get("run_model_provider") or "",
                "run_model_id": task.get("run_model_id") or "",
                "created_at": task.get("task_created_at"),
                "updated_at": task.get("task_updated_at"),
                "latest_activity_at": latest_activity_at,
                "has_usage": int(totals.get("request_count") or 0) > 0,
            }

        for task_id, totals in grouped.items():
            finalize_price_lines(totals)
            results_by_task[task_id] = {
                **totals,
                "task_id": int(task_id) if task_id.isdigit() else task_id,
                "session_id": None,
                "title": f"Task #{task_id}",
                "task_status": "unknown",
                "session_status": "",
                "latest_attempt_id": None,
                "latest_attempt_no": None,
                "latest_attempt_status": "",
                "run_model_provider": "",
                "run_model_id": "",
                "created_at": None,
                "updated_at": None,
                "latest_activity_at": int(totals.get("last_usage_at") or 0),
                "has_usage": True,
            }

        return sorted(
            results_by_task.values(),
            key=lambda item: (
                int(item.get("sell_micros") or item.get("charge_micros") or 0),
                int(item.get("provider_cost_micros") or item.get("cost_micros") or 0),
                int(item.get("latest_activity_at") or 0),
            ),
            reverse=True,
        )

    def _attempt_scope(self, task: dict[str, Any], attempts: list[dict[str, Any]], attempt: str) -> tuple[list[int] | None, dict[str, Any]]:
        requested = str(attempt or "all").strip().lower()
        latest_attempt_id = int(task.get("latest_attempt_id") or 0)
        attempt_ids = [int(item.get("id") or 0) for item in attempts if int(item.get("id") or 0)]
        if requested in {"", "all"}:
            return None, {"requested": "all", "mode": "all", "attempt_ids": attempt_ids}
        if requested == "latest":
            selected = [latest_attempt_id] if latest_attempt_id else []
            return selected, {"requested": "latest", "mode": "latest", "attempt_ids": selected}
        try:
            selected_id = int(requested)
        except ValueError:
            selected_id = 0
        selected = [selected_id] if selected_id else []
        return selected, {"requested": requested, "mode": "attempt", "attempt_ids": selected}

    def _task_warnings(self, *, task: dict[str, Any], attempts: list[dict[str, Any]], rows: list[dict[str, Any]], totals: dict[str, Any], scope: dict[str, Any]) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        latest_attempt_id = int(task.get("latest_attempt_id") or 0)
        latest_attempt = next((item for item in attempts if int(item.get("id") or 0) == latest_attempt_id), None)
        latest_started_at = int((latest_attempt or {}).get("started_at") or (latest_attempt or {}).get("created_at") or 0)
        scope_mode = str(scope.get("mode") or "")
        all_scope = scope_mode == "all"
        latest_scope = scope_mode == "latest"
        if latest_attempt_id and (all_scope or latest_scope):
            latest_count = len([row for row in rows if str(row.get("attempt_id") or "") == str(latest_attempt_id)])
            if latest_count == 0:
                warnings.append(
                    {
                        "code": "latest_attempt_has_no_usage",
                        "severity": "warning",
                        "message": f"Latest attempt {latest_attempt_id} has no local usage rows in this scope.",
                        "attempt_id": latest_attempt_id,
                    }
                )
        if all_scope and latest_attempt_id and latest_started_at:
            suspicious = [
                row
                for row in rows
                if str(row.get("attempt_id") or "") not in {"", str(latest_attempt_id)}
                and int(row.get("created_at") or 0) >= latest_started_at
            ]
            if suspicious:
                warnings.append(
                    {
                        "code": "usage_after_latest_start_attributed_to_older_attempt",
                        "severity": "warning",
                        "message": "Some usage rows were created after the latest attempt started but are attributed to an older attempt.",
                        "latest_attempt_id": latest_attempt_id,
                        "row_count": len(suspicious),
                    }
                )
        missing_attempt_count = len([row for row in rows if not str(row.get("attempt_id") or "").strip()])
        if missing_attempt_count:
            warnings.append(
                {
                    "code": "usage_missing_attempt_id",
                    "severity": "warning",
                    "message": "Some usage rows have no attempt_id.",
                    "row_count": missing_attempt_count,
                }
            )
        if int(totals.get("unpriced_count") or 0) > 0:
            warnings.append(
                {
                    "code": "unpriced_usage_rows",
                    "severity": "info",
                    "message": "Some usage rows have no provider actual cost and no local pricebook estimate.",
                    "row_count": int(totals.get("unpriced_count") or 0),
                }
            )
        missing_units_count = len([row for row in rows if not (row.get("units") or {})])
        if missing_units_count:
            warnings.append(
                {
                    "code": "usage_missing_billable_units",
                    "severity": "info",
                    "message": "Some usage rows have no normalized billable units.",
                    "row_count": missing_units_count,
                }
            )
        return warnings

    def report(self, *, days: int = 30, limit: int = 200) -> dict[str, Any]:
        days = max(1, min(int(days), 3650))
        limit = max(1, min(int(limit), 1000))
        since = now_ms() - days * 86_400_000
        with self.engine.begin() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    text(
                        f"""
SELECT {USAGE_SELECT_COLUMNS}
FROM local_usage_log
WHERE created_at >= :since
ORDER BY created_at DESC, id DESC
"""
                    ),
                    {"since": since},
                )
                .mappings()
                .all()
            ]
        enriched = [enrich_usage_row(row) for row in rows]
        totals = summarize_rows(enriched)
        return {
            "schema_version": "1.0",
            "generated_at": now_ms(),
            "window": {"days": days, "since": since, "limit": limit},
            "currency": "USD",
            "pricing_mode": PRICEBOOK_VERSION,
            "totals": totals,
            "by_task": self._tasks_overview(enriched, since=since),
            "by_modality": group_rows(enriched, ("modality",)),
            "by_provider_model": group_rows(enriched, ("provider", "model_id", "modality")),
            "items": enriched[:limit],
            "item_count": len(enriched),
            "pricebook": pricebook_rows(),
            "pricebook_note": "Provider actual cost is used when available; otherwise this report uses the local pricebook estimate. Raw units are the accounting source of truth.",
        }

    def task_report(self, task_id: int, *, attempt: str = "all", limit: int = 500, include_items: bool = True) -> dict[str, Any]:
        task_id = int(task_id)
        limit = max(1, min(int(limit), 2000))
        task = self._task_metadata(task_id)
        if not task:
            return {
                "schema_version": "1.0",
                "generated_at": now_ms(),
                "currency": "USD",
                "pricing_mode": PRICEBOOK_VERSION,
                "found": False,
                "task": None,
                "attempt_scope": {"requested": str(attempt or "all"), "mode": "all", "attempt_ids": []},
                "attempts": [],
                "totals": summarize_rows([]),
                "by_action": [],
                "by_step": [],
                "by_provider_model": [],
                "items": [],
                "item_count": 0,
                "warnings": [{"code": "task_not_found", "severity": "warning", "message": f"Task {task_id} was not found."}],
            }
        attempts = self._task_attempts(task_id)
        attempt_ids, scope = self._attempt_scope(task, attempts, attempt)
        rows = self._task_usage_rows(task_id, attempt_ids=attempt_ids)
        enriched = [enrich_usage_row(row) for row in rows]
        totals = summarize_rows(enriched)
        warnings = self._task_warnings(task=task, attempts=attempts, rows=enriched, totals=totals, scope=scope)
        by_action = group_rows(enriched, ("step_id", "provider", "model_id", "modality"))
        for row in by_action:
            row["action"] = str(row.get("model_id") or row.get("provider") or row.get("modality") or "")
        return {
            "schema_version": "1.0",
            "generated_at": now_ms(),
            "currency": "USD",
            "pricing_mode": PRICEBOOK_VERSION,
            "found": True,
            "task": {
                "task_id": task.get("task_id"),
                "session_id": task.get("session_id"),
                "title": task.get("session_title") or f"Task #{task_id}",
                "task_status": task.get("task_status") or "",
                "session_status": task.get("session_status") or "",
                "latest_attempt_id": task.get("latest_attempt_id"),
                "latest_attempt_no": task.get("latest_attempt_no"),
                "latest_attempt_status": task.get("latest_attempt_status") or "",
                "run_model_provider": task.get("run_model_provider") or "",
                "run_model_id": task.get("run_model_id") or "",
                "workspace_dir": task.get("workspace_dir") or "",
                "created_at": task.get("task_created_at"),
                "updated_at": task.get("task_updated_at"),
            },
            "attempt_scope": scope,
            "attempts": attempts,
            "totals": totals,
            "by_action": by_action,
            "by_step": group_rows(enriched, ("step_id",)),
            "by_provider_model": group_rows(enriched, ("provider", "model_id", "modality")),
            "items": enriched[:limit] if include_items else [],
            "item_count": len(enriched),
            "warnings": warnings,
            "pricebook_note": "Provider actual cost is used when available; otherwise this report uses the local pricebook estimate. Raw units are the accounting source of truth.",
        }
