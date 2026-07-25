from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Callable

from opcrew_backend.routes.media_model_config import load_config
from opcrew_backend.services.tts_voice_aliases import normalize_storyboard_tts_selection, resolve_tts_voice_alias

TTS_BUILDER_CANDIDATES_REL = "SessionOutput/tts/tts_builder_candidates.json"
CLOUD_VOICE_CLONES_REL = "SessionOutput/tts/cloud_voice_clones.json"
_REDACTED_RUNTIME_VALUES = {"", "[model]", "[provider]", "model", "provider"}
_RUNTIME_FIELDS = ("provider", "model", "target_model", "source_clone_provider")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _runtime_value(value: Any) -> str:
    result = _text(value)
    return "" if result.lower() in _REDACTED_RUNTIME_VALUES else result


def _record_voice(record: dict[str, Any]) -> str:
    return _text(record.get("voice_id") or record.get("voice") or record.get("selected_voice_id"))


def _record_candidate_id(record: dict[str, Any]) -> str:
    return _text(record.get("candidate_id"))


def _record_is_operational(record: dict[str, Any]) -> bool:
    for key in ("enabled", "active", "has_api_key"):
        if key not in record:
            continue
        value = record.get(key)
        if value is False or _text(value).lower() in {"0", "false", "no", "off"}:
            return False
    return True


def _record_runtime(record: dict[str, Any]) -> dict[str, str]:
    provider = _runtime_value(record.get("provider") or record.get("source_clone_provider"))
    model = _runtime_value(record.get("model") or record.get("target_model"))
    voice_id = _record_voice(record)
    if not provider or not model or not voice_id:
        return {}
    result = {
        "provider": provider,
        "model": model,
        "voice_id": voice_id,
    }
    target_model = _runtime_value(record.get("target_model"))
    source_clone_provider = _runtime_value(record.get("source_clone_provider"))
    if target_model:
        result["target_model"] = target_model
    if source_clone_provider:
        result["source_clone_provider"] = source_clone_provider
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _selection_records(selection: dict[str, Any]) -> list[dict[str, Any]]:
    records = [selection]
    for key in ("top_candidates", "recommendations"):
        records.extend(item for item in (selection.get(key) or []) if isinstance(item, dict))
    return records


def _payload_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in ("candidates", "clones", "top_candidates", "recommendations"):
        records.extend(item for item in (payload.get(key) or []) if isinstance(item, dict))
    for key in ("selected_tts_candidate", "selected_candidate", "tts_builder_selection"):
        item = payload.get(key)
        if isinstance(item, dict):
            records.append(item)
    selection = payload.get("storyboard_tts_selection")
    if isinstance(selection, dict):
        records.extend(_selection_records(selection))
    default_clone = payload.get("default_voice_clone_config")
    if isinstance(default_clone, dict):
        records.append(default_clone)
    talking_head_voice = payload.get("talking_head_voice")
    if isinstance(talking_head_voice, dict):
        clone_config = talking_head_voice.get("clone_config")
        if isinstance(clone_config, dict):
            records.append({
                **talking_head_voice,
                **clone_config,
                "voice_id": _record_voice(talking_head_voice) or _record_voice(clone_config),
            })
    return records


def trusted_storyboard_tts_records(
    workspace: Path,
    *,
    read_json: Callable[[Path], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    reader = read_json or _read_json
    payloads = [
        reader(workspace / TTS_BUILDER_CANDIDATES_REL),
        reader(workspace / CLOUD_VOICE_CLONES_REL),
        reader(workspace / "SessionContext/Variables.json"),
    ]
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for record in _payload_records(payload):
            if not _record_is_operational(record):
                continue
            runtime = _record_runtime(record)
            if not runtime:
                continue
            key = (
                _record_candidate_id(record),
                runtime["voice_id"],
                runtime["provider"],
                runtime["model"],
            )
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
    return records


def _safe_normal_voice_token(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", _text(value)).strip("_")[:64] or "voice"


def configured_normal_voice_defaults(ctx: Any, item: dict[str, Any]) -> dict[str, str]:
    candidate_id = _record_candidate_id(item)
    requested_voice = _record_voice(item)
    if not candidate_id and not requested_voice:
        return {}
    try:
        config = load_config(ctx, "tts")
    except Exception:
        return {}
    providers = [provider for provider in (config.get("providers") or []) if isinstance(provider, dict)]
    any_key_configured = any(bool(provider.get("has_api_key")) for provider in providers)
    matches: list[dict[str, str]] = []
    for provider in providers:
        if provider.get("enabled") is False:
            continue
        if any_key_configured and not provider.get("has_api_key"):
            continue
        provider_id = _text(provider.get("provider"))
        if not provider_id:
            continue
        selected_by_model = provider.get("selected_voice_by_model") if isinstance(provider.get("selected_voice_by_model"), dict) else {}
        for model_item in (provider.get("models") or []):
            if not isinstance(model_item, dict) or model_item.get("enabled") is False:
                continue
            model = _text(model_item.get("model"))
            if not model:
                continue
            voices = [
                _text(voice.get("voice_id") or voice.get("id") or voice.get("value"))
                for voice in (model_item.get("voices") or [])
                if isinstance(voice, dict)
            ]
            voices = [voice for voice in voices if voice]
            default_voice = _text(selected_by_model.get(model)) or (voices[0] if voices else requested_voice)
            prefix = f"normal_{_safe_normal_voice_token(provider_id)}_{_safe_normal_voice_token(model)}_"
            if candidate_id and candidate_id.startswith(prefix):
                voice_token = candidate_id[len(prefix):]
                voice_id = requested_voice if requested_voice and (not voices or requested_voice in voices) else ""
                if not voice_id:
                    voice_id = next((voice for voice in voices if _safe_normal_voice_token(voice) == voice_token), "")
                return {"provider": provider_id, "model": model, "voice_id": voice_id or default_voice}
            if requested_voice and requested_voice in voices:
                matches.append({"provider": provider_id, "model": model, "voice_id": requested_voice})
    if not matches:
        return {}
    active_provider = _text(config.get("active_provider"))
    return next((match for match in matches if match["provider"] == active_provider), matches[0])


def _unique_runtime(records: list[dict[str, Any]]) -> dict[str, str]:
    runtimes: dict[tuple[str, str, str], dict[str, str]] = {}
    for record in records:
        runtime = _record_runtime(record)
        if runtime:
            runtimes[(runtime["provider"], runtime["model"], runtime["voice_id"])] = runtime
    return next(iter(runtimes.values())) if len(runtimes) == 1 else {}


def _trusted_runtime_for(item: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, str]:
    candidate_id = _record_candidate_id(item)
    voice_id = _record_voice(item)
    if candidate_id:
        runtime = _unique_runtime([record for record in records if _record_candidate_id(record) == candidate_id])
        if runtime:
            return runtime
    if voice_id:
        runtime = _unique_runtime([record for record in records if _record_voice(record) == voice_id])
        if runtime:
            return runtime
    return {}


def _trusted_alias_runtime(ctx: Any, item: dict[str, Any]) -> dict[str, str]:
    target = resolve_tts_voice_alias(ctx, _record_voice(item))
    if not target:
        return {}
    provider = _runtime_value(target.get("provider"))
    model = _runtime_value(target.get("model"))
    voice_id = _text(target.get("voice_id"))
    return {"provider": provider, "model": model, "voice_id": voice_id} if provider and model and voice_id else {}


def _rehydrate_record(
    ctx: Any,
    item: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    result = copy.deepcopy(item)
    runtime = _trusted_alias_runtime(ctx, result) or _trusted_runtime_for(result, records) or configured_normal_voice_defaults(ctx, result)
    for field in _RUNTIME_FIELDS:
        result.pop(field, None)
    if not runtime:
        if strict:
            raise ValueError("Selected TTS voice has no trusted server-side runtime mapping")
        return result
    result["provider"] = runtime["provider"]
    result["model"] = runtime["model"]
    if runtime.get("target_model"):
        result["target_model"] = runtime.get("target_model") or runtime["model"]
    if runtime.get("source_clone_provider") or _text(result.get("voice_source")).lower() == "cloud_clone":
        result["source_clone_provider"] = runtime.get("source_clone_provider") or runtime["provider"]
    if runtime.get("voice_id"):
        result["voice_id"] = runtime["voice_id"]
        result["voice"] = runtime["voice_id"]
    return result


def rehydrate_storyboard_tts_selection(
    ctx: Any,
    workspace: Path,
    plan: dict[str, Any],
    *,
    active_clone_provider: str = "",
    strict: bool = False,
    read_json: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Restore hidden TTS runtime fields from trusted server-side state.

    Customer payloads intentionally omit provider/model identifiers. Those fields
    must never be required from the browser when saving or executing a storyboard.
    """

    normalized = copy.deepcopy(plan)
    selection = normalized.get("storyboard_tts_selection")
    if not isinstance(selection, dict):
        return normalize_storyboard_tts_selection(
            ctx,
            normalized,
            active_clone_provider=active_clone_provider,
            strict=strict,
        )
    records = trusted_storyboard_tts_records(workspace, read_json=read_json)
    selection = _rehydrate_record(ctx, selection, records, strict=strict)
    for key in ("top_candidates", "recommendations"):
        rows = selection.get(key)
        if isinstance(rows, list):
            recovered_rows = [
                _rehydrate_record(ctx, item, records)
                for item in rows
                if isinstance(item, dict)
            ]
            # The browser may send presentation metadata for candidates, but it
            # cannot introduce executable provider/model/voice combinations.
            selection[key] = [item for item in recovered_rows if _record_runtime(item)]
    normalized["storyboard_tts_selection"] = selection
    return normalize_storyboard_tts_selection(
        ctx,
        normalized,
        active_clone_provider=active_clone_provider,
        strict=strict,
    )
