from __future__ import annotations

import copy
import hashlib
import hmac
import secrets
import threading
from typing import Any


PUBLIC_TTS_VOICE_PREFIX = "tts_voice_"
TTS_VOICE_ALIAS_STATE_KEY = "model_alias.tts_voice.v1"
TTS_VOICE_ALIAS_FALLBACK_SECRET = "opencrew-tts-voice-alias-test-fallback-v1"

_VOICE_FIELDS = ("voice_id", "voice_clone_id", "voice", "selected_voice_id")
_STATE_LOCK = threading.RLock()
_REDACTED_VALUES = {"", "[model]", "[provider]", "model", "provider"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _score(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _clean_model_identifier(value: Any) -> str:
    identifier = _text(value)
    lowered = identifier.lower()
    if "[model]" in lowered or "[provider]" in lowered:
        return ""
    return identifier


def _normalize_clone_provider_identifier(value: Any) -> str:
    provider = _clean_model_identifier(value)
    lowered = provider.lower()
    if lowered in {"aliyun", "aliyun_dashscope", "dashscope", "qwen", "cosyvoice"}:
        return "cosyvoice"
    if lowered == "heygen":
        return "heygen"
    if lowered in {"minimax", "minimaxi", "hailuo"}:
        return "minimax"
    return provider


def _runtime_provider_identifier(provider: Any, model: Any) -> str:
    model_id = _clean_model_identifier(model).lower()
    if model_id.startswith("cosyvoice-"):
        return "cosyvoice"
    if model_id.startswith("heygen-"):
        return "heygen"
    if model_id.startswith("minimax-"):
        return "minimax"
    return _normalize_clone_provider_identifier(provider)


def storyboard_tts_candidate_is_cloud_clone(item: dict[str, Any]) -> bool:
    voice_id = _text(item.get("voice_id") or item.get("voice"))
    model = _clean_model_identifier(item.get("model") or item.get("target_model")).lower()
    return (
        _text(item.get("voice_source")).lower() == "cloud_clone"
        or bool(_text(item.get("source_clone_provider")))
        or _text(item.get("candidate_id")).startswith("clone_")
        or "voice-clone" in model
        or voice_id.lower().startswith("cosyvoice-")
    )


def storyboard_tts_candidate_is_inactive_cloud_clone(
    item: dict[str, Any],
    active_clone_provider: str,
) -> bool:
    if not storyboard_tts_candidate_is_cloud_clone(item):
        return False
    active_provider = _runtime_provider_identifier(active_clone_provider, "").lower()
    if not active_provider:
        return False
    model = _clean_model_identifier(item.get("model") or item.get("target_model"))
    provider = _runtime_provider_identifier(item.get("provider") or item.get("source_clone_provider"), model).lower()
    return bool(provider and provider != active_provider)


def _stable_voice_alias(secret: str, voice_id: str) -> str:
    token = hmac.new(
        secret.encode("utf-8"),
        f"voice:{voice_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20]
    return f"{PUBLIC_TTS_VOICE_PREFIX}{token}"


def _read_state(ctx: Any, *, create: bool) -> tuple[dict[str, Any], bool]:
    getter = getattr(ctx, "get_setting", None)
    setter = getattr(ctx, "set_setting", None)
    stored = getter(TTS_VOICE_ALIAS_STATE_KEY, {}) if callable(getter) else {}
    state = copy.deepcopy(stored) if isinstance(stored, dict) else {}
    changed = False
    if create and not _text(state.get("secret")):
        state["secret"] = secrets.token_urlsafe(32) if callable(setter) else TTS_VOICE_ALIAS_FALLBACK_SECRET
        changed = True
    if not isinstance(state.get("targets"), dict):
        state["targets"] = {}
        changed = create
    return state, changed


def _cloud_voice_record(value: dict[str, Any], voice_id: str) -> bool:
    if not voice_id:
        return False
    if _text(value.get("voice_source")).lower() == "cloud_clone":
        return True
    if _text(value.get("source_clone_provider")):
        return True
    if _text(value.get("candidate_id")).startswith("clone_"):
        return True
    target_model = _text(value.get("target_model"))
    if target_model:
        return True
    model = _text(value.get("model")).lower()
    provider = _text(value.get("provider")).lower()
    if voice_id.lower().startswith("cosyvoice-"):
        return True
    return (
        "voice-clone" in model
        or (provider == "cosyvoice" and model.startswith("cosyvoice-"))
        or provider in {"heygen", "minimax", "minimaxi"}
    )


def alias_customer_tts_voices(ctx: Any, value: Any) -> Any:
    """Replace cloud-clone identifiers with stable customer-safe aliases.

    The reverse mapping stays in app settings so customer requests can be resolved
    without exposing provider model names or provider-owned voice identifiers.
    """

    with _STATE_LOCK:
        state, changed = _read_state(ctx, create=True)
        secret = _text(state.get("secret")) or TTS_VOICE_ALIAS_FALLBACK_SECRET
        targets = state["targets"]

        def register(
            voice_id: str,
            *,
            provider: str = "",
            model: str = "",
            candidate_id: str = "",
        ) -> str:
            nonlocal changed
            if voice_id.startswith(PUBLIC_TTS_VOICE_PREFIX):
                return voice_id
            alias = _stable_voice_alias(secret, voice_id)
            current = targets.get(alias) if isinstance(targets.get(alias), dict) else {}
            target_model = _clean_model_identifier(model) or _clean_model_identifier(current.get("model"))
            target_provider = _runtime_provider_identifier(provider, target_model) or _runtime_provider_identifier(current.get("provider"), target_model)
            target = {
                "voice_id": voice_id,
                "provider": target_provider,
                "model": target_model,
                "candidate_id": candidate_id or _text(current.get("candidate_id")),
            }
            if current != target:
                targets[alias] = target
                changed = True
            return alias

        def transform(item: Any, inherited_provider: str = "", inherited_model: str = "") -> Any:
            if isinstance(item, list):
                return [transform(child, inherited_provider, inherited_model) for child in item]
            if not isinstance(item, dict):
                return item

            provider = _text(item.get("provider") or item.get("source_clone_provider")) or inherited_provider
            model = _text(item.get("target_model") or item.get("model")) or inherited_model
            voice_id = next((_text(item.get(key)) for key in _VOICE_FIELDS if _text(item.get(key))), "")
            candidate_id = _text(item.get("candidate_id"))
            is_cloud_voice = _cloud_voice_record(item, voice_id)
            result = {
                key: transform(child, provider, model)
                for key, child in item.items()
            }
            if not is_cloud_voice or voice_id.startswith(PUBLIC_TTS_VOICE_PREFIX):
                return result

            alias = register(
                voice_id,
                provider=provider,
                model=model,
                candidate_id=candidate_id,
            )
            for key in _VOICE_FIELDS:
                if _text(item.get(key)):
                    result[key] = alias
            if candidate_id.startswith("clone_"):
                result["candidate_id"] = f"clone_{alias}"
            for key in ("voice_name", "name", "label", "voice_label"):
                if _text(item.get(key)) == voice_id:
                    result[key] = f"Cloud Voice {alias[-6:]}"
            if not any(_text(result.get(key)) for key in ("voice_name", "name", "label", "voice_label")):
                result["voice_name"] = f"Cloud Voice {alias[-6:]}"
            return result

        payload = transform(copy.deepcopy(value))
        if changed:
            setter = getattr(ctx, "set_setting", None)
            if callable(setter):
                setter(TTS_VOICE_ALIAS_STATE_KEY, state)
        return payload


def resolve_tts_voice_alias(ctx: Any, voice_id: Any) -> dict[str, str] | None:
    alias = _text(voice_id)
    if not alias.startswith(PUBLIC_TTS_VOICE_PREFIX):
        return None
    with _STATE_LOCK:
        state, _ = _read_state(ctx, create=False)
        target = state.get("targets", {}).get(alias) if isinstance(state.get("targets"), dict) else None
    if not isinstance(target, dict) or not _text(target.get("voice_id")):
        return None
    model = _clean_model_identifier(target.get("model"))
    return {
        "voice_id": _text(target.get("voice_id")),
        "provider": _runtime_provider_identifier(target.get("provider"), model),
        "model": model,
        "candidate_id": _text(target.get("candidate_id")),
    }


def resolve_tts_voice_aliases_in_payload(ctx: Any, value: Any, *, strict: bool = False) -> Any:
    """Resolve public voice aliases before persisting or sending provider requests."""

    if isinstance(value, list):
        return [resolve_tts_voice_aliases_in_payload(ctx, item, strict=strict) for item in value]
    if not isinstance(value, dict):
        return value

    result = {
        key: resolve_tts_voice_aliases_in_payload(ctx, item, strict=strict)
        for key, item in value.items()
    }
    alias = next(
        (
            _text(value.get(key))
            for key in _VOICE_FIELDS
            if _text(value.get(key)).startswith(PUBLIC_TTS_VOICE_PREFIX)
        ),
        "",
    )
    if not alias:
        return result
    target = resolve_tts_voice_alias(ctx, alias)
    if not target:
        if strict:
            raise ValueError("Unknown cloud voice alias")
        return result

    for key in _VOICE_FIELDS:
        if _text(value.get(key)) == alias:
            result[key] = target["voice_id"]
    candidate_id = _text(value.get("candidate_id"))
    if candidate_id == f"clone_{alias}" and target.get("candidate_id"):
        result["candidate_id"] = target["candidate_id"]
    if target.get("provider"):
        result["provider"] = target["provider"]
        if "source_clone_provider" in value or _text(value.get("voice_source")) == "cloud_clone":
            result["source_clone_provider"] = target["provider"]
    if target.get("model"):
        result["model"] = target["model"]
        if "target_model" in value:
            result["target_model"] = target["model"]
    return result


def normalize_storyboard_tts_selection(
    ctx: Any,
    plan: dict[str, Any],
    *,
    active_clone_provider: str = "",
    strict: bool = False,
) -> dict[str, Any]:
    """Resolve, filter, and deduplicate StoryBoard TTS recommendations."""

    payload = resolve_tts_voice_aliases_in_payload(ctx, copy.deepcopy(plan), strict=strict)
    selection = payload.get("storyboard_tts_selection") if isinstance(payload.get("storyboard_tts_selection"), dict) else None
    if selection is None:
        return payload

    active_provider = _runtime_provider_identifier(active_clone_provider, "").lower()
    groups = [
        selection.get("top_candidates") if isinstance(selection.get("top_candidates"), list) else [],
        selection.get("recommendations") if isinstance(selection.get("recommendations"), list) else [],
    ]
    candidates: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            voice_id = _text(item.get("voice_id") or item.get("voice"))
            if voice_id.lower() in _REDACTED_VALUES or voice_id.startswith(PUBLIC_TTS_VOICE_PREFIX):
                continue
            model = _clean_model_identifier(item.get("model") or item.get("target_model")).lower()
            provider = _runtime_provider_identifier(item.get("provider") or item.get("source_clone_provider"), model).lower()
            is_cloud_clone = storyboard_tts_candidate_is_cloud_clone(item)
            if is_cloud_clone and active_provider and provider and provider != active_provider:
                continue
            key = f"voice|{voice_id.lower()}" if voice_id else f"candidate|{_text(item.get('candidate_id')).lower()}"
            if key in {"voice|", "candidate|"}:
                continue
            if key in index_by_key:
                existing_index = index_by_key[key]
                existing_score = _score(candidates[existing_index].get("score") or candidates[existing_index].get("match_score"))
                item_score = _score(item.get("score") or item.get("match_score"))
                if item_score > existing_score:
                    candidates[existing_index] = item
                continue
            index_by_key[key] = len(candidates)
            candidates.append(item)

    selection["top_candidates"] = candidates
    selection["recommendations"] = candidates
    if candidates:
        selected_voice = _text(selection.get("voice_id") or selection.get("voice")).lower()
        selected_candidate_id = _text(selection.get("candidate_id")).lower()
        selected = next(
            (
                item
                for item in candidates
                if (selected_voice and _text(item.get("voice_id") or item.get("voice")).lower() == selected_voice)
                or (selected_candidate_id and _text(item.get("candidate_id")).lower() == selected_candidate_id)
            ),
            candidates[0],
        )
        voice_id = _text(selected.get("voice_id") or selected.get("voice"))
        selected_model = _clean_model_identifier(selected.get("model") or selected.get("target_model"))
        selected_provider = _runtime_provider_identifier(selected.get("provider") or selected.get("source_clone_provider"), selected_model)
        selected_source_provider = _runtime_provider_identifier(selected.get("source_clone_provider"), selected_model) or selected_provider
        selection.update({
            "provider": selected_provider,
            "model": selected_model,
            "voice_id": voice_id,
            "voice": voice_id,
            "candidate_id": _text(selected.get("candidate_id")),
            "voice_source": _text(selected.get("voice_source") or selection.get("voice_source")),
            "source_clone_provider": selected_source_provider,
            "voice_label": _text(selected.get("voice_label") or selected.get("label") or selection.get("voice_label") or voice_id),
            "label": _text(selected.get("label") or selected.get("voice_label") or selection.get("label") or voice_id),
        })
    else:
        selected_provider = _runtime_provider_identifier(
            selection.get("provider") or selection.get("source_clone_provider"),
            selection.get("model") or selection.get("target_model"),
        ).lower()
        selected_model = _text(selection.get("model") or selection.get("target_model")).lower()
        selected_voice = _text(selection.get("voice_id") or selection.get("voice")).lower()
        selected_is_cloud_clone = (
            _text(selection.get("voice_source")).lower() == "cloud_clone"
            or bool(_text(selection.get("source_clone_provider")))
            or _text(selection.get("candidate_id")).startswith("clone_")
            or "voice-clone" in selected_model
            or selected_voice.startswith("cosyvoice-")
        )
        if selected_is_cloud_clone and active_provider and selected_provider and selected_provider != active_provider:
            for key in (
                "provider",
                "provider_label",
                "model",
                "model_label",
                "target_model",
                "voice_id",
                "voice_clone_id",
                "voice",
                "selected_voice_id",
                "candidate_id",
                "voice_source",
                "source_clone_provider",
                "voice_label",
                "label",
            ):
                selection.pop(key, None)
    payload["storyboard_tts_selection"] = selection
    return payload
