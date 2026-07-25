from __future__ import annotations

import hashlib
import math
import re
import time
from pathlib import Path
from typing import Any

from opcrew_backend.services.local_metering import PRICEBOOK_VERSION


def _text(value: Any, default: str = "") -> str:
    if value is None or value == "":
        value = default
    return str(value or "").strip()


def _positive_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _millis(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(number if number > 10_000_000_000 else number * 1000)


def _non_space_len(value: Any) -> int:
    return len(re.sub(r"\s+", "", _text(value)))


def approximate_tokens(value: Any) -> int:
    text = _text(value)
    if not text:
        return 0
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    non_cjk = len(re.sub(r"[\u3400-\u9fff\s]+", "", text))
    return max(1, cjk + math.ceil(non_cjk / 4))


def stable_usage_request_id(prefix: str, *parts: Any) -> str:
    seed = "|".join(_text(part) for part in parts if _text(part))
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    safe_prefix = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", _text(prefix, "koubo_usage")).strip("_") or "koubo_usage"
    return f"{safe_prefix}_{digest}"


def usage_result_dict(value: Any) -> dict[str, Any]:
    return {
        "request_id": _text(getattr(value, "request_id", "")),
        "inserted": bool(getattr(value, "inserted", False)),
        "local_usage_id": _text(getattr(value, "local_usage_id", "")),
    }


def record_storyboard_usage(
    ctx: Any,
    task: dict[str, Any],
    *,
    request_id: str,
    provider: str,
    model_id: str,
    modality: str,
    units: dict[str, Any],
    step_id: str,
    status: str = "ok",
    error_code: str = "",
    started_at: Any = None,
    finished_at: Any = None,
    actual_cost_micros: int | None = None,
    estimated_cost_micros: int | None = None,
    actual_cost_source: str = "",
    actual_cost_raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recorder = getattr(ctx, "local_usage", None)
    record_with_result = getattr(recorder, "record_with_result", None)
    if record_with_result is None:
        return {"request_id": request_id, "inserted": False, "local_usage_id": ""}
    task_id = _text(task.get("id") or task.get("task_id"))
    attempt_id = _text(task.get("latest_attempt_id"))
    req_id = _text(request_id) or stable_usage_request_id("koubo_usage", task_id, attempt_id, provider, model_id, modality, step_id, time.time())
    idempotency_key = f"koubo:{task_id}:{attempt_id}:{step_id}:{req_id}"
    result = record_with_result(
        provider=_text(provider, "unknown"),
        model_id=_text(model_id, "unknown"),
        modality=_text(modality, "unknown"),
        status=status,
        request_id=req_id,
        task_id=task_id or None,
        attempt_id=attempt_id or None,
        step_id=step_id,
        idempotency_key=idempotency_key,
        units={key: value for key, value in (units or {}).items() if value not in (None, "", 0, 0.0)},
        est_cost_micros=estimated_cost_micros,
        actual_cost_micros=actual_cost_micros,
        actual_cost_currency="USD" if actual_cost_source else "",
        actual_cost_source=actual_cost_source,
        actual_cost_raw=actual_cost_raw or {},
        pricebook_version=PRICEBOOK_VERSION,
        error_code=error_code,
        started_at=_millis(started_at),
        finished_at=_millis(finished_at),
    )
    return usage_result_dict(result)


def tts_usage_units(text_value: Any, *, prompt: Any = "", audio_seconds: Any = 0, output_bytes: Any = 0) -> dict[str, Any]:
    units: dict[str, Any] = {
        "request": 1,
        "character": _non_space_len(text_value),
        "prompt_character": _non_space_len(prompt),
        "audio_second_observed": round(_positive_number(audio_seconds), 3),
    }
    try:
        byte_count = int(output_bytes or 0)
    except (TypeError, ValueError):
        byte_count = 0
    if byte_count > 0:
        units["output_bytes_observed"] = byte_count
    return units


def image_usage_units(*, count: Any = 1, prompt: Any = "", reference_count: Any = 0) -> dict[str, Any]:
    return {
        "request": 1,
        "image": max(1, int(_positive_number(count) or 1)),
        "prompt_character": _non_space_len(prompt),
        "reference": int(_positive_number(reference_count) or 0),
    }


def video_usage_units(*, seconds: Any, prompt: Any = "", reference_count: Any = 0, resolution: Any = "") -> dict[str, Any]:
    resolution_value = _text(resolution).lower()
    seconds_key = f"video_{resolution_value}_second" if resolution_value in {"480p", "720p", "1080p"} else "video_second"
    return {
        "request": 1,
        seconds_key: round(_positive_number(seconds), 3),
        "prompt_character": _non_space_len(prompt),
        "reference": int(_positive_number(reference_count) or 0),
    }


def chat_usage_units(*, input_text: Any, output_text: Any = "", system_text: Any = "") -> dict[str, Any]:
    combined_input = f"{_text(system_text)}\n{_text(input_text)}".strip()
    return {
        "request": 1,
        "approx_input_tokens": approximate_tokens(combined_input),
        "approx_output_tokens": approximate_tokens(output_text),
        "input_character": _non_space_len(combined_input),
        "output_character": _non_space_len(output_text),
    }


def voice_clone_usage_units(audio_path: Path | None = None, *, audio_seconds: Any = 0) -> dict[str, Any]:
    units = {"request": 1, "audio_second_observed": round(_positive_number(audio_seconds), 3)}
    if audio_path is not None:
        try:
            units["input_bytes_observed"] = int(audio_path.stat().st_size)
        except OSError:
            pass
    return units
