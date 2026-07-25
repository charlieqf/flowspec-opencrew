from __future__ import annotations

import copy
import hashlib
import hmac
import secrets
from typing import Any

from fastapi import HTTPException

from opcrew_backend.routes.media_model_config import load_config


PUBLIC_TTS_PROVIDER_PREFIX = "tts_provider_"
PUBLIC_TTS_MODEL_SEGMENT = "_model_"
TTS_PUBLIC_ALIAS_STATE_KEY = "model_alias.tts.v1"
TTS_PUBLIC_ALIAS_FALLBACK_SECRET = "opencrew-tts-public-alias-test-fallback-v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _legacy_provider_alias(index: int) -> str:
    return f"{PUBLIC_TTS_PROVIDER_PREFIX}{index:02d}"


def _legacy_model_alias(provider_alias: str, index: int) -> str:
    return f"{provider_alias}{PUBLIC_TTS_MODEL_SEGMENT}{index:02d}"


def _stable_token(secret: str, purpose: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), f"{purpose}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _provider_alias(provider: str, secret: str) -> str:
    return f"{PUBLIC_TTS_PROVIDER_PREFIX}{_stable_token(secret, 'provider', provider.lower())}"


def _model_alias(provider_alias: str, provider: str, model: str, secret: str) -> str:
    return f"{provider_alias}{PUBLIC_TTS_MODEL_SEGMENT}{_stable_token(secret, 'model', f'{provider.lower()}::{model}')}"


def _provider_label(index: int) -> str:
    return f"TTS Provider {index:02d}"


def _model_label(provider_index: int, model_index: int) -> str:
    return f"TTS Model {provider_index:02d}-{model_index:02d}"


def _provider_default_model(provider: dict[str, Any]) -> str:
    stored_model = _text(provider.get("model"))
    models = provider.get("models") if isinstance(provider.get("models"), list) else []
    return stored_model or (_text(models[0].get("model")) if models and isinstance(models[0], dict) else "")


def _legacy_alias_targets(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    targets: dict[str, dict[str, str]] = {}
    providers = config.get("providers") if isinstance(config.get("providers"), list) else []
    for provider_index, provider in enumerate(providers, start=1):
        if not isinstance(provider, dict):
            continue
        real_provider = _text(provider.get("provider")).lower()
        if not real_provider:
            continue
        legacy_provider = _legacy_provider_alias(provider_index)
        targets[legacy_provider] = {"provider": real_provider, "model": _provider_default_model(provider)}
        models = provider.get("models") if isinstance(provider.get("models"), list) else []
        for model_index, model in enumerate(models, start=1):
            if not isinstance(model, dict):
                continue
            real_model = _text(model.get("model"))
            if real_model:
                targets[f"{legacy_provider}::{_legacy_model_alias(legacy_provider, model_index)}"] = {
                    "provider": real_provider,
                    "model": real_model,
                }
    return targets


def tts_public_alias_state(ctx: Any, config: dict[str, Any]) -> dict[str, Any]:
    getter = getattr(ctx, "get_setting", None)
    setter = getattr(ctx, "set_setting", None)
    stored = getter(TTS_PUBLIC_ALIAS_STATE_KEY, {}) if callable(getter) else {}
    state = copy.deepcopy(stored) if isinstance(stored, dict) else {}
    changed = False
    secret = _text(state.get("secret"))
    if not secret:
        secret = secrets.token_urlsafe(32) if callable(setter) else TTS_PUBLIC_ALIAS_FALLBACK_SECRET
        state["secret"] = secret
        changed = True
    legacy_targets = state.get("legacy_targets") if isinstance(state.get("legacy_targets"), dict) else {}
    legacy_targets = copy.deepcopy(legacy_targets)
    for alias, target in _legacy_alias_targets(config).items():
        if alias not in legacy_targets:
            legacy_targets[alias] = target
            changed = True
    state["legacy_targets"] = legacy_targets
    if changed and callable(setter):
        setter(TTS_PUBLIC_ALIAS_STATE_KEY, state)
    return state


def attach_tts_public_aliases(config: dict[str, Any], *, alias_secret: str = TTS_PUBLIC_ALIAS_FALLBACK_SECRET) -> dict[str, Any]:
    payload = copy.deepcopy(config) if isinstance(config, dict) else {}
    providers = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    active_provider = _text(payload.get("active_provider")).lower()
    active_public_provider = ""
    for provider_index, provider in enumerate(providers, start=1):
        if not isinstance(provider, dict):
            continue
        real_provider = _text(provider.get("provider")).lower()
        public_provider = _provider_alias(real_provider, alias_secret)
        provider["public_provider"] = public_provider
        provider["provider_alias"] = public_provider
        if _text(provider.get("provider")).lower() == active_provider:
            active_public_provider = public_provider
        models = provider.get("models") if isinstance(provider.get("models"), list) else []
        selected_by_model = provider.get("selected_voice_by_model") if isinstance(provider.get("selected_voice_by_model"), dict) else {}
        selected_by_public_model: dict[str, Any] = {}
        for model_index, model in enumerate(models, start=1):
            if not isinstance(model, dict):
                continue
            public_model = _model_alias(public_provider, real_provider, _text(model.get("model")), alias_secret)
            model["public_model"] = public_model
            model["model_alias"] = public_model
            real_model = _text(model.get("model"))
            if real_model and real_model in selected_by_model:
                selected_by_public_model[public_model] = selected_by_model[real_model]
        provider["selected_voice_by_public_model"] = selected_by_public_model
    if active_public_provider:
        payload["active_public_provider"] = active_public_provider
    return payload


def customer_tts_public_config(config: dict[str, Any], *, alias_secret: str = TTS_PUBLIC_ALIAS_FALLBACK_SECRET) -> dict[str, Any]:
    payload = attach_tts_public_aliases(config, alias_secret=alias_secret)
    providers = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    active_public_provider = _text(payload.get("active_public_provider"))
    public_providers: list[dict[str, Any]] = []
    for provider_index, provider in enumerate(providers, start=1):
        if not isinstance(provider, dict):
            continue
        public_provider = _text(provider.get("public_provider") or provider.get("provider_alias"))
        selected_by_public_model = (
            copy.deepcopy(provider.get("selected_voice_by_public_model"))
            if isinstance(provider.get("selected_voice_by_public_model"), dict)
            else {}
        )
        public_provider_payload = {
            "provider": public_provider,
            "public_provider": public_provider,
            "provider_alias": public_provider,
            "provider_label": _provider_label(provider_index),
            "label": _provider_label(provider_index),
            "has_api_key": bool(provider.get("has_api_key")),
            "enabled": bool(provider.get("enabled", True)),
            "selected_voice_by_public_model": selected_by_public_model,
            "selected_voice_by_model": selected_by_public_model,
            "models": [],
        }
        models = provider.get("models") if isinstance(provider.get("models"), list) else []
        for model_index, model in enumerate(models, start=1):
            if not isinstance(model, dict):
                continue
            public_model = _text(model.get("public_model") or model.get("model_alias"))
            model_payload = {
                "model": public_model,
                "public_model": public_model,
                "model_alias": public_model,
                "label": _model_label(provider_index, model_index),
                "model_label": _model_label(provider_index, model_index),
                "enabled": bool(model.get("enabled", True)),
                "voices": copy.deepcopy(model.get("voices")) if isinstance(model.get("voices"), list) else [],
            }
            for key in ("voice_modes", "languages", "language", "default_voice", "selected_voice"):
                if key in model:
                    model_payload[key] = copy.deepcopy(model.get(key))
            public_provider_payload["models"].append(model_payload)
        public_providers.append(public_provider_payload)
    return {
        "kind": "tts",
        "active_provider": active_public_provider,
        "active_public_provider": active_public_provider,
        "providers": public_providers,
        "source": "Connection active TTS model",
    }


def resolve_tts_public_alias_from_config(
    config: dict[str, Any],
    provider: str,
    model: str,
    *,
    legacy_targets: dict[str, Any] | None = None,
) -> tuple[str, str]:
    provider_value = _text(provider).lower()
    model_value = _text(model)
    if provider_value == "gemini":
        provider_value = "google"
    legacy_key = f"{provider_value}::{model_value}" if model_value else provider_value
    legacy_target = (legacy_targets or {}).get(legacy_key)
    if isinstance(legacy_target, dict):
        legacy_provider = _text(legacy_target.get("provider")).lower()
        legacy_model = _text(legacy_target.get("model"))
        for provider_item in config.get("providers") or []:
            if not isinstance(provider_item, dict) or _text(provider_item.get("provider")).lower() != legacy_provider:
                continue
            models = provider_item.get("models") if isinstance(provider_item.get("models"), list) else []
            valid_models = {_text(item.get("model")) for item in models if isinstance(item, dict)}
            if not legacy_model or legacy_model in valid_models:
                return legacy_provider, legacy_model
            return "", ""
        return "", ""
    public_alias_requested = provider_value.startswith(PUBLIC_TTS_PROVIDER_PREFIX) or model_value.startswith(PUBLIC_TTS_PROVIDER_PREFIX)
    providers = config.get("providers") if isinstance(config.get("providers"), list) else []
    for provider_item in providers:
        if not isinstance(provider_item, dict):
            continue
        real_provider = _text(provider_item.get("provider")).lower()
        public_provider = _text(provider_item.get("public_provider") or provider_item.get("provider_alias")).lower()
        if provider_value not in {real_provider, public_provider}:
            continue
        models = provider_item.get("models") if isinstance(provider_item.get("models"), list) else []
        stored_model = _text(provider_item.get("model"))
        if not model_value:
            return real_provider, stored_model or (_text(models[0].get("model")) if models and isinstance(models[0], dict) else "")
        for model_item in models:
            if not isinstance(model_item, dict):
                continue
            real_model = _text(model_item.get("model"))
            public_model = _text(model_item.get("public_model") or model_item.get("model_alias"))
            if model_value in {real_model, public_model}:
                return real_provider, real_model
        if public_alias_requested:
            return "", ""
        return real_provider, model_value
    if public_alias_requested:
        return "", ""
    return provider_value, model_value


def resolve_tts_public_alias(ctx: Any, provider: str, model: str) -> tuple[str, str]:
    raw_config = load_config(ctx, "tts")
    state = tts_public_alias_state(ctx, raw_config)
    config = attach_tts_public_aliases(raw_config, alias_secret=_text(state.get("secret")) or TTS_PUBLIC_ALIAS_FALLBACK_SECRET)
    resolved_provider, resolved_model = resolve_tts_public_alias_from_config(
        config,
        provider,
        model,
        legacy_targets=state.get("legacy_targets") if isinstance(state.get("legacy_targets"), dict) else {},
    )
    if not resolved_provider or (_text(model) and not resolved_model):
        raise HTTPException(status_code=400, detail="Select a valid TTS model before generating.")
    return resolved_provider, resolved_model
