from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from opcrew_backend.context import now_ms
from opcrew_backend.model_policy import SURFACE_KOUBO_ASSET_AGENT_CHAT
from opcrew_backend.routes.auth import AUTH_ROLE_USER
from opcrew_backend.routes.media_model_config import CONFIG_TABLE as MEDIA_CONFIG_TABLE
from opcrew_backend.routes.media_model_config import default_api_key_ref as media_default_api_key_ref
from opcrew_backend.routes.media_model_config import ensure_table as ensure_media_config_table
from opcrew_backend.routes.media_model_config import load_stored_key as load_media_stored_key

from .constants import *
from . import asset_search_providers
from .text_utils import redact_secret_text
from .asset_search_providers import (
    AssetSearchProviderError,
    AssetSearchRateLimit,
    AssetSearchSecurityError,
    provider_for,
    validate_provider_url,
)


ASSET_SEARCH_ROOT_REL = "SessionContext/AssetSearchAgent"
ASSET_SEARCH_SETTINGS_REL = f"{ASSET_SEARCH_ROOT_REL}/Settings.json"
ASSET_SEARCH_RUNS_REL = f"{ASSET_SEARCH_ROOT_REL}/SearchRuns"
ASSET_SEARCH_IMPORTS_REL = f"{ASSET_SEARCH_ROOT_REL}/Imports"
ASSET_SEARCH_CACHE_REL = f"{ASSET_SEARCH_ROOT_REL}/Cache"
ASSET_SEARCH_SOURCE_LIST_JSON_REL = "SessionOutput/storyboard/asset_search_source_list.json"
ASSET_SEARCH_SOURCE_LIST_MD_REL = "SessionOutput/storyboard/asset_search_source_list.md"
ASSET_SEARCH_SETTINGS_SCHEMA = "koubo_asset_search_settings_0.1"
ASSET_SEARCH_RUN_SCHEMA = "koubo_asset_search_run_0.1"
ASSET_SEARCH_IMPORT_SCHEMA = "koubo_asset_search_import_0.1"
ASSET_SEARCH_CANDIDATE_SCHEMA = "koubo_asset_search_candidate_0.1"
ASSET_SEARCH_SOURCE_LIST_SCHEMA = "koubo_asset_search_source_list_0.1"
ASSET_SEARCH_PROVIDERS = (
    "local",
    "media_library",
    "pexels",
    "pixabay",
    "wikimedia",
    "unsplash",
)
ASSET_SEARCH_MEDIA_TYPES = ("image", "video", "audio")
ASSET_SEARCH_KEYED_PROVIDERS = ("pexels", "pixabay", "unsplash")
ASSET_SEARCH_MAX_IMPORT_COUNT = 12
ASSET_SEARCH_PIXABAY_TTL_MS = 24 * 60 * 60 * 1000
ASSET_SEARCH_DEFAULT_TTL_MS = 6 * 60 * 60 * 1000
ASSET_SEARCH_PLANNER_TIMEOUT_SECONDS = 45
ASSET_SEARCH_FILE_SIZE_LIMITS = {
    "image": 30 * 1024 * 1024,
    "video": 500 * 1024 * 1024,
    "audio": 100 * 1024 * 1024,
}
WIKIMEDIA_QUERY_NOISE_TERMS = {
    "realistic",
    "documentary",
    "stock",
    "media",
    "footage",
    "broll",
    "roll",
    "video",
    "photo",
    "image",
    "picture",
    "horizontal",
    "vertical",
    "landscape",
    "portrait",
    "cinematic",
    "style",
    "hd",
    "uhd",
    "4k",
}

PLANNER_SYSTEM_PROMPT = """
You are Koubo Asset Library Search Planner.
Return strict JSON only. Do not browse the web, do not invent media URLs, and do not include Markdown.
Create concise English search queries for public stock-media APIs.
The JSON must include request_id, media_types, aspect, sources, queries, negative_terms, must_have, nice_to_have, style, license_policy, and risk_notes.
""".strip()


def _text(value: Any, fallback: str = "") -> str:
    if value is None or value == "":
        value = fallback
    return str(value or "").strip()


def _int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value or fallback)
    except Exception:
        return fallback


def _bool(value: Any, fallback: bool = False) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _safe_error_detail(value: Any, limit: int = 500, *, sc: Any) -> str:
    redacted = sc.redact_secret_text(_text(value))
    redacted = re.sub(r"(https?://[^\s\"'<>?]+)\?[^ \t\r\n\"'<>]+", r"\1?***", redacted)
    return redacted[:limit]


SERVICE_EXPORTS = (
    "read_asset_search_settings",
    "save_asset_search_settings",
    "asset_search_provider_status",
    "create_asset_search_plan",
    "stream_asset_search_events",
    "list_asset_search_runs",
    "load_asset_search_run",
    "import_asset_search_candidates",
    "create_storyboard_asset_search_plan",
    "asset_search_source_list",
    "export_asset_search_source_list",
    "asset_search_cache_key",
    "normalize_asset_search_candidate",
    "asset_search_candidate_identity",
    "asset_search_provider_ttl_ms",
    "asset_search_provider_query_variants",
    "asset_search_manifest_existing_candidate",
    "asset_search_target_rel_path",
    "asset_search_validate_import_prefix",
)


def register_asset_search_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])


def default_asset_search_settings() -> dict[str, Any]:
    return {
        "schema_version": ASSET_SEARCH_SETTINGS_SCHEMA,
        "sources": {
            "local": {"enabled": True, "limit_per_query": 12},
            "media_library": {"enabled": True, "limit_per_query": 12},
            "pexels": {"enabled": True, "limit_per_query": 12},
            "pixabay": {"enabled": True, "limit_per_query": 12},
            "wikimedia": {"enabled": True, "limit_per_query": 12},
            "unsplash": {"enabled": False, "limit_per_query": 12},
        },
        "defaults": {
            "media_types": ["image", "video", "audio"],
            "aspect": "auto",
            "language": "auto",
            "safe_search": True,
            "max_candidates": 36,
            "auto_download": False,
            "require_user_import_confirmation": True,
        },
        "ranking": {
            "trust_provider_order": True,
            "prefer_exact_aspect": True,
            "prefer_high_resolution": True,
            "prefer_open_license": True,
            "prefer_short_video_broll": True,
            "embedding_rerank": True,
        },
    }


def normalize_asset_search_settings(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    settings = default_asset_search_settings()
    raw_sources = source.get("sources") if isinstance(source.get("sources"), dict) else {}
    for provider, config in settings["sources"].items():
        raw = raw_sources.get(provider) if isinstance(raw_sources.get(provider), dict) else {}
        config["enabled"] = _bool(raw.get("enabled"), bool(config["enabled"]))
        config["limit_per_query"] = max(1, min(_int(raw.get("limit_per_query"), int(config["limit_per_query"])), 24))
    raw_defaults = source.get("defaults") if isinstance(source.get("defaults"), dict) else {}
    media_types = normalize_media_types(raw_defaults.get("media_types"), settings["defaults"]["media_types"])
    settings["defaults"]["media_types"] = media_types
    settings["defaults"]["aspect"] = normalize_aspect(raw_defaults.get("aspect"), settings["defaults"]["aspect"])
    language = _text(raw_defaults.get("language"), settings["defaults"]["language"]).lower()
    settings["defaults"]["language"] = language if language == "auto" or re.fullmatch(r"[a-z]{2}", language) else "auto"
    settings["defaults"]["safe_search"] = _bool(raw_defaults.get("safe_search"), True)
    settings["defaults"]["max_candidates"] = max(1, min(_int(raw_defaults.get("max_candidates"), 36), 72))
    settings["defaults"]["auto_download"] = False
    settings["defaults"]["require_user_import_confirmation"] = True
    ranking = source.get("ranking") if isinstance(source.get("ranking"), dict) else {}
    for key, value in settings["ranking"].items():
        settings["ranking"][key] = _bool(ranking.get(key), bool(value))
    return settings


def normalize_media_types(value: Any, fallback: list[str] | None = None) -> list[str]:
    values = value if isinstance(value, list) else []
    result = []
    for item in values:
        kind = _text(item).lower()
        if kind in ASSET_SEARCH_MEDIA_TYPES and kind not in result:
            result.append(kind)
    return result or list(fallback or ["image", "video"])


def normalize_sources(value: Any, settings: dict[str, Any] | None = None) -> list[str]:
    configured = settings or default_asset_search_settings()
    values = value if isinstance(value, list) else []
    result = []
    for item in values:
        provider = _text(item).lower()
        source_cfg = (configured.get("sources") or {}).get(provider) if isinstance(configured.get("sources"), dict) else None
        if provider in ASSET_SEARCH_PROVIDERS and provider not in result and (not isinstance(source_cfg, dict) or source_cfg.get("enabled") is not False):
            result.append(provider)
    if result:
        return result
    return [
        provider
        for provider in ASSET_SEARCH_PROVIDERS
        if ((configured.get("sources") or {}).get(provider) or {}).get("enabled") is not False
    ]


def normalize_aspect(value: Any, fallback: str = "auto") -> str:
    aspect = _text(value, fallback).lower().replace(" ", "")
    aliases = {
        "horizontal": "16:9",
        "landscape": "16:9",
        "vertical": "9:16",
        "portrait": "9:16",
        "square": "1:1",
    }
    aspect = aliases.get(aspect, aspect)
    return aspect if aspect in {"auto", "16:9", "9:16", "1:1", "4:3", "3:4"} else fallback


def read_asset_search_settings(task: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    return normalize_asset_search_settings(sc.read_json(sc.workspace_for(task) / ASSET_SEARCH_SETTINGS_REL))


def save_asset_search_settings(task: dict[str, Any], payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    settings = normalize_asset_search_settings(payload.get("settings") if isinstance(payload.get("settings"), dict) else payload)
    workspace = sc.workspace_for(task)
    sc.write_json(workspace / ASSET_SEARCH_SETTINGS_REL, settings)
    save_asset_search_provider_keys(payload, sc=sc)
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.settings.saved", {
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "sources": {key: {"enabled": bool(value.get("enabled"))} for key, value in settings["sources"].items()},
    })
    return settings


def _provider_env_key(provider: str) -> str:
    return {
        "pexels": "PEXELS_API_KEY",
        "pixabay": "PIXABAY_API_KEY",
        "unsplash": "UNSPLASH_ACCESS_KEY",
    }.get(_text(provider).lower(), "")


def _asset_search_secret_ref(provider: str) -> str:
    return media_default_api_key_ref("asset_search", _text(provider).lower())


def _asset_search_stored_key(provider: str, *, sc: Any) -> str:
    provider = _text(provider).lower()
    context = getattr(sc, "ctx", None)
    if context is not None:
        try:
            value = _text(context.secret_store.get(_asset_search_secret_ref(provider)))
            if value:
                return value
        except Exception:
            pass
        try:
            value = load_media_stored_key(context, "asset_search", provider)
            if value:
                return value
        except Exception:
            pass
    env_name = _provider_env_key(provider)
    return _text(os.environ.get(env_name)) if env_name else ""


def save_asset_search_provider_keys(payload: dict[str, Any], *, sc: Any) -> None:
    keys = payload.get("provider_keys") if isinstance(payload, dict) else None
    if not isinstance(keys, dict):
        return
    context = getattr(sc, "ctx", None)
    if context is None:
        return
    ensure_media_config_table(context)
    changed: list[str] = []
    for provider in ASSET_SEARCH_KEYED_PROVIDERS:
        api_key = _text(keys.get(provider))
        if not api_key:
            continue
        api_key_ref = _asset_search_secret_ref(provider)
        context.secret_store.set(api_key_ref, api_key)
        with context.engine.begin() as conn:
            conn.execute(
                sql_text(f"""
INSERT INTO {MEDIA_CONFIG_TABLE} (kind, provider, enabled, active, model, api_key_ciphertext, api_key_ref, extra_json, created_at, updated_at)
VALUES ('asset_search', :provider, TRUE, FALSE, :model, NULL, :api_key_ref, :extra_json, now(), now())
ON CONFLICT (kind, provider) DO UPDATE SET
  enabled = TRUE,
  active = FALSE,
  model = EXCLUDED.model,
  api_key_ciphertext = NULL,
  api_key_ref = EXCLUDED.api_key_ref,
  extra_json = EXCLUDED.extra_json,
  updated_at = EXCLUDED.updated_at
"""),
                {
                    "provider": provider,
                    "model": provider,
                    "api_key_ref": api_key_ref,
                    "extra_json": json.dumps({"configured_from": "asset_search_settings"}, ensure_ascii=True),
                },
            )
        changed.append(provider)
    if changed:
        context.event("info", "asset-search", "Asset search provider credentials saved", {"providers": changed})


def _provider_credentials(*, sc: Any) -> dict[str, str]:
    return {
        "local": "",
        "media_library": "",
        "pexels": _asset_search_stored_key("pexels", sc=sc),
        "pixabay": _asset_search_stored_key("pixabay", sc=sc),
        "wikimedia": "",
        "unsplash": _asset_search_stored_key("unsplash", sc=sc),
    }


def asset_search_provider_status(settings: dict[str, Any] | None = None, *, sc: Any) -> dict[str, Any]:
    config = normalize_asset_search_settings(settings or {})
    credentials = _provider_credentials(sc=sc)
    return {
        provider: {
            "enabled": bool((config["sources"].get(provider) or {}).get("enabled")),
            "configured": bool(
                provider in {"local", "media_library", "wikimedia"}
                or credentials.get(provider)
            ),
        }
        for provider in ASSET_SEARCH_PROVIDERS
    }


def normalize_asset_search_request(payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    defaults = settings.get("defaults") if isinstance(settings.get("defaults"), dict) else {}
    text_value = _text(payload.get("text") or payload.get("user_text"))
    media_types = normalize_media_types(payload.get("media_types"), defaults.get("media_types") if isinstance(defaults.get("media_types"), list) else None)
    sources = normalize_sources(payload.get("sources"), settings)
    aspect = normalize_aspect(payload.get("aspect"), _text(defaults.get("aspect"), "auto"))
    limit = max(1, min(_int(payload.get("limit_per_source") or payload.get("limit"), 12), 24))
    language = _text(payload.get("language") or defaults.get("language") or "auto").lower()
    if language == "auto":
        language = "en"
    return {
        "user_text": text_value,
        "media_types": media_types,
        "aspect": aspect,
        "sources": sources,
        "limit_per_source": limit,
        "language": language if re.fullmatch(r"[a-z]{2}", language) else "en",
        "safe_search": _bool(payload.get("safe_search"), _bool(defaults.get("safe_search"), True)),
        "selected_assets": payload.get("selected_assets") if isinstance(payload.get("selected_assets"), list) else [],
        "selected_storyboard_context": payload.get("selected_storyboard_context") if isinstance(payload.get("selected_storyboard_context"), dict) else {},
        "provider": _text(payload.get("provider") or payload.get("planner_provider")),
        "model": _text(payload.get("model") or payload.get("planner_model")),
    }


def _contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value or ""))


def translate_search_text_to_english_keywords(value: str) -> tuple[str, bool]:
    raw = _text(value)
    lowered = raw.lower()
    replacements = {
        "\u6625\u6696\u82b1\u5f00": "spring blossoms flowers warm spring",
        "\u6625\u6696": "warm spring",
        "\u82b1\u5f00": "flowers blooming blossoms",
        "\u5f00\u82b1": "flowering blooming blossoms",
        "\u6625\u5929": "spring",
        "\u6625\u65e5": "spring day",
        "\u9c9c\u82b1": "flowers",
        "\u82b1\u6735": "flowers blossoms",
        "\u533b\u9662": "hospital",
        "\u8d70\u5eca": "corridor hallway",
        "\u533b\u751f": "doctor",
        "\u62a4\u58eb": "nurse",
        "\u5e73\u677f": "tablet",
        "\u624b\u673a": "phone",
        "\u6a2a\u5c4f": "landscape 16:9",
        "\u7ad6\u5c4f": "portrait 9:16",
        "\u771f\u5b9e": "realistic",
        "\u7eaa\u5f55\u7247": "documentary",
        "\u98ce\u683c": "style",
        "\u89c6\u9891": "video",
        "\u97f3\u9891": "audio",
        "\u97f3\u4e50": "music",
        "\u914d\u4e50": "background music",
        "\u97f3\u6548": "sound effect",
        "\u58f0\u97f3": "sound",
        "\u56fe\u7247": "photo image",
        "\u7167\u7247": "photo",
        "\u4eba\u7269": "person",
        "\u529e\u516c\u5ba4": "office",
        "\u57ce\u5e02": "city",
        "\u81ea\u7136": "nature",
        "\u4ea7\u54c1": "product",
        "\u53a8\u623f": "kitchen",
        "\u5ba4\u5185": "indoor",
        "\u5ba4\u5916": "outdoor",
    }
    translated = lowered
    used = False
    for source, target in replacements.items():
        if source in translated:
            translated = translated.replace(source, f" {target} ")
            used = True
    tokens = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", translated)
    if tokens:
        return " ".join(tokens[:12]), used or not _contains_cjk(raw)
    return raw, False


def _dedupe_query_variants(items: list[str], limit: int = 4) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = re.sub(r"\s+", " ", _text(item).strip())
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _planner_query_hint(user_text: str) -> str:
    translated, translated_ok = translate_search_text_to_english_keywords(user_text)
    return translated if translated_ok and _contains_cjk(user_text) else ""


def _merge_query_hint(query: str, hint: str) -> str:
    raw_query = _text(query)
    raw_hint = _text(hint)
    if not raw_hint:
        return raw_query
    query_tokens = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", raw_query.lower())
    hint_tokens = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", raw_hint.lower())
    if raw_query and hint_tokens:
        query_set = set(query_tokens)
        hint_set = set(hint_tokens)
        covered = sum(1 for token in hint_set if token in query_set)
        if covered >= max(3, int(len(hint_set) * 0.7)):
            return raw_query
    tokens: list[str] = []
    seen: set[str] = set()
    for token in [*hint_tokens, *query_tokens]:
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= 16:
            break
    return " ".join(tokens) or raw_query


def _query_variants_for_user_text(user_text: str, query: str) -> list[str]:
    variants: list[str] = []
    raw = _text(user_text)
    if _contains_cjk(raw):
        variants.append(raw[:80])
    variants.append(_merge_query_hint(query, _planner_query_hint(raw)))
    variants.append(_text(query))
    return _dedupe_query_variants(variants, 3)


def _infer_media_types(value: str, requested: list[str]) -> list[str]:
    if requested:
        return requested
    lowered = value.lower()
    if "audio" in lowered or "music" in lowered or "sound effect" in lowered or "\u97f3\u9891" in value or "\u97f3\u4e50" in value or "\u914d\u4e50" in value or "\u97f3\u6548" in value:
        return ["audio"]
    if "video" in lowered or "\u89c6\u9891" in value or "b-roll" in lowered:
        return ["video"]
    if "photo" in lowered or "image" in lowered or "\u56fe\u7247" in value or "\u7167\u7247" in value:
        return ["image"]
    return ["image", "video"]


def _infer_aspect(value: str, requested: str) -> str:
    if requested and requested != "auto":
        return requested
    lowered = value.lower()
    if "16:9" in lowered or "landscape" in lowered or "horizontal" in lowered or "\u6a2a\u5c4f" in value:
        return "16:9"
    if "9:16" in lowered or "portrait" in lowered or "vertical" in lowered or "\u7ad6\u5c4f" in value:
        return "9:16"
    if "1:1" in lowered or "square" in lowered or "\u65b9\u56fe" in value:
        return "1:1"
    return requested or "auto"


def fallback_asset_search_plan(request: dict[str, Any]) -> dict[str, Any]:
    translated, translated_ok = translate_search_text_to_english_keywords(request["user_text"])
    query = translated or request["user_text"] or "realistic stock media"
    media_types = _infer_media_types(request["user_text"], request.get("media_types") or [])
    aspect = _infer_aspect(request["user_text"], request.get("aspect") or "auto")
    queries = [
        {
            "query": query,
            "language": "en" if translated_ok else request.get("language") or "en",
            "query_variants": _query_variants_for_user_text(request["user_text"], query),
            "media_type": media_type,
            "priority": index + 1,
        }
        for index, media_type in enumerate(media_types)
    ]
    return {
        "media_types": media_types,
        "aspect": aspect,
        "sources": request.get("sources") or list(ASSET_SEARCH_PROVIDERS),
        "queries": queries,
        "negative_terms": ["cartoon", "animation"] if "realistic" in query else [],
        "must_have": [part for part in query.split()[:4] if len(part) > 2],
        "nice_to_have": [part for part in query.split()[4:8] if len(part) > 2],
        "style": "realistic stock media",
        "license_policy": "prefer_open_or_free_commercial",
        "risk_notes": [],
        "degraded": bool(_contains_cjk(request["user_text"]) and not translated_ok),
    }


def _planner_user_prompt(request_id: str, task: dict[str, Any], request: dict[str, Any], *, sc: Any) -> str:
    try:
        plan, _meta = sc.load_plan(task, sc=sc)
        storyboard_text = "\n".join(
            _text(dialogue.get("text"))
            for shot in plan.get("shots") or []
            for scene in shot.get("scenes") or []
            for dialogue in scene.get("dialogues") or []
            if isinstance(dialogue, dict)
        )[:2000]
    except Exception:
        storyboard_text = ""
    payload = {
        "request_id": request_id,
        "user_text": request["user_text"],
        "task_context": {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "storyboard_text_preview": storyboard_text,
            "selected_storyboard_context": request.get("selected_storyboard_context") or {},
        },
        "settings": {
            "media_types": request.get("media_types"),
            "aspect": request.get("aspect"),
            "sources": request.get("sources"),
            "language": request.get("language"),
        },
        "available_sources": list(ASSET_SEARCH_PROVIDERS),
    }
    return (
        "Build an asset-search query plan for this JSON request. "
        "Return only JSON with the same request_id.\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _extract_json_object(value: str) -> dict[str, Any] | None:
    raw = _text(value)
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        raw = match.group(0)
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def normalize_planner_result(value: dict[str, Any], request: dict[str, Any], request_id: str) -> dict[str, Any]:
    if _text(value.get("request_id")) != request_id:
        raise ValueError("planner response request_id did not match")
    media_types = normalize_media_types(value.get("media_types"), request.get("media_types") or ["image", "video"])
    aspect = normalize_aspect(value.get("aspect"), request.get("aspect") or "auto")
    sources = normalize_sources(value.get("sources") or request.get("sources"), {"sources": {provider: {"enabled": True} for provider in ASSET_SEARCH_PROVIDERS}})
    query_hint = _planner_query_hint(request.get("user_text") or "")
    queries = []
    for item in value.get("queries") if isinstance(value.get("queries"), list) else []:
        if not isinstance(item, dict):
            continue
        query = _merge_query_hint(_text(item.get("query")), query_hint)
        media_type = _text(item.get("media_type")).lower()
        if query and media_type in ASSET_SEARCH_MEDIA_TYPES:
            queries.append({
                "query": query,
                "language": _text(item.get("language"), "en")[:8],
                "query_variants": _query_variants_for_user_text(request.get("user_text") or "", query),
                "media_type": media_type,
                "priority": max(1, _int(item.get("priority"), len(queries) + 1)),
            })
    if not queries:
        queries = fallback_asset_search_plan({**request, "media_types": media_types, "aspect": aspect}).get("queries") or []
    return {
        "summary": _text(value.get("summary")),
        "media_types": media_types,
        "aspect": aspect,
        "sources": sources,
        "queries": queries[:8],
        "negative_terms": [_text(item) for item in value.get("negative_terms") or [] if _text(item)][:20],
        "must_have": [_text(item) for item in value.get("must_have") or [] if _text(item)][:20],
        "nice_to_have": [_text(item) for item in value.get("nice_to_have") or [] if _text(item)][:20],
        "style": _text(value.get("style")),
        "license_policy": _text(value.get("license_policy"), "prefer_open_or_free_commercial"),
        "risk_notes": [_text(item) for item in value.get("risk_notes") or [] if _text(item)][:10],
        "degraded": False,
    }


def _query_storyboard_ref(item: dict[str, Any]) -> dict[str, str]:
    source = item.get("storyboard_ref") if isinstance(item.get("storyboard_ref"), dict) else item
    result = {
        "shot_id": _text(source.get("shot_id")),
        "scene_id": _text(source.get("scene_id")),
        "dialogue_id": _text(source.get("dialogue_id")),
        "label": _text(source.get("label")),
    }
    return {key: value for key, value in result.items() if value}


def normalize_supplied_asset_search_plan(value: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    media_types = normalize_media_types(value.get("media_types"), request.get("media_types") or ["image", "video", "audio"])
    aspect = normalize_aspect(value.get("aspect"), request.get("aspect") or "auto")
    sources = normalize_sources(value.get("sources") or request.get("sources"), {"sources": {provider: {"enabled": True} for provider in ASSET_SEARCH_PROVIDERS}})
    queries: list[dict[str, Any]] = []
    for item in value.get("queries") if isinstance(value.get("queries"), list) else []:
        if not isinstance(item, dict):
            continue
        query = _text(item.get("query"))
        media_type = _text(item.get("media_type")).lower()
        if query and media_type in ASSET_SEARCH_MEDIA_TYPES:
            normalized = {
                "query": query,
                "language": _text(item.get("language"), "en")[:8] or "en",
                "media_type": media_type,
                "priority": max(1, _int(item.get("priority"), len(queries) + 1)),
            }
            storyboard_ref = _query_storyboard_ref(item)
            if storyboard_ref:
                normalized["storyboard_ref"] = storyboard_ref
            queries.append(normalized)
    if not queries:
        return fallback_asset_search_plan({**request, "media_types": media_types, "aspect": aspect})
    return {
        "summary": _text(value.get("summary")),
        "media_types": media_types,
        "aspect": aspect,
        "sources": sources,
        "queries": queries[:8],
        "negative_terms": [_text(item) for item in value.get("negative_terms") or [] if _text(item)][:20],
        "must_have": [_text(item) for item in value.get("must_have") or [] if _text(item)][:20],
        "nice_to_have": [_text(item) for item in value.get("nice_to_have") or [] if _text(item)][:20],
        "style": _text(value.get("style")),
        "license_policy": _text(value.get("license_policy"), "prefer_open_or_free_commercial"),
        "risk_notes": [_text(item) for item in value.get("risk_notes") or [] if _text(item)][:10],
        "degraded": bool(value.get("degraded")),
        "batch_scope": _text(value.get("batch_scope")),
        "edited": True,
    }


def _storyboard_query_text(*parts: Any) -> str:
    raw = " ".join(_text(part) for part in parts if _text(part))
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""
    translated, translated_ok = translate_search_text_to_english_keywords(raw)
    return translated if translated_ok and translated else raw[:160]


def _scene_dialogue_text(scene: dict[str, Any]) -> str:
    values: list[str] = []
    for dialogue in scene.get("dialogues") or scene.get("dialogue_items") or []:
        if isinstance(dialogue, dict):
            values.append(_text(dialogue.get("text") or dialogue.get("dialogue") or dialogue.get("line")))
    return " ".join(value for value in values if value)


def _storyboard_batch_query_items(plan: dict[str, Any], media_types: list[str], max_queries: int) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    shots = plan.get("shots") if isinstance(plan.get("shots"), list) else []
    for shot_index, shot in enumerate([item for item in shots if isinstance(item, dict)], start=1):
        shot_id = _text(shot.get("shot_id") or shot.get("id"), f"shot_{shot_index:03d}")
        shot_label = _text(shot.get("title") or shot.get("summary") or shot.get("description") or shot.get("name"), shot_id)
        scenes = shot.get("scenes") if isinstance(shot.get("scenes"), list) else []
        if not scenes:
            scenes = [{"scene_id": "", "description": shot_label, "dialogues": shot.get("dialogues") or []}]
        for scene_index, scene in enumerate([item for item in scenes if isinstance(item, dict)], start=1):
            scene_id = _text(scene.get("scene_id") or scene.get("id"), f"{shot_id}_scene_{scene_index:02d}")
            scene_label = _text(scene.get("title") or scene.get("summary") or scene.get("description") or scene.get("visual") or scene.get("image_prompt"))
            query_text = _storyboard_query_text(shot_label, scene_label, _scene_dialogue_text(scene))
            if not query_text:
                continue
            for media_type in media_types:
                queries.append({
                    "query": query_text,
                    "language": "en",
                    "media_type": media_type,
                    "priority": len(queries) + 1,
                    "storyboard_ref": {
                        "shot_id": shot_id,
                        "scene_id": scene_id,
                        "label": scene_label or shot_label,
                    },
                })
                if len(queries) >= max_queries:
                    return queries
    return queries


def _build_storyboard_asset_search_plan_sync(task: dict[str, Any], payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    settings = read_asset_search_settings(task, sc=sc)
    request = normalize_asset_search_request({**(payload or {}), "text": _text((payload or {}).get("text"), "storyboard asset search")}, settings)
    max_queries = max(1, min(_int((payload or {}).get("max_queries"), 8), 24))
    try:
        storyboard_plan, _meta = sc.load_plan(task, sc=sc)
        queries = _storyboard_batch_query_items(storyboard_plan, request["media_types"], max_queries)
        if not queries:
            raise ValueError("storyboard has no searchable shot or scene text")
        result = {
            "summary": "Storyboard batch asset search",
            "media_types": request["media_types"],
            "aspect": request["aspect"],
            "sources": request["sources"],
            "queries": queries,
            "negative_terms": ["cartoon", "animation"],
            "must_have": [],
            "nice_to_have": [],
            "style": "match StoryBoard shot and scene intent",
            "license_policy": "prefer_open_or_free_commercial",
            "risk_notes": [],
            "degraded": False,
            "batch_scope": "storyboard",
            "edited": True,
        }
    except Exception as exc:
        result = fallback_asset_search_plan(request)
        result["summary"] = "Storyboard batch asset search fallback"
        result["batch_scope"] = "storyboard"
        result["edited"] = True
        result["degraded"] = True
        result["planner_error"] = _safe_error_detail(exc, sc=sc)
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.plan.created", {
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "planner": "storyboard_batch",
        "query_count": len(result.get("queries") or []),
        "media_types": result.get("media_types") or [],
        "sources": result.get("sources") or [],
        "degraded": bool(result.get("degraded")),
    })
    return result


async def create_storyboard_asset_search_plan(task: dict[str, Any], payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    return await asyncio.to_thread(_build_storyboard_asset_search_plan_sync, task, payload or {}, sc=sc)


def _build_asset_search_plan_sync(task: dict[str, Any], payload: dict[str, Any], role: str = AUTH_ROLE_USER, *, sc: Any) -> dict[str, Any]:
    settings = read_asset_search_settings(task, sc=sc)
    request = normalize_asset_search_request(payload, settings)
    request_id = f"asset_search_{now_ms()}_{uuid.uuid4().hex[:8]}"
    fallback = fallback_asset_search_plan(request)
    if not request["user_text"]:
        return fallback
    try:
        session_row = sc.safe_session(int(task["session_id"]))
        opencode_session_id = _text(session_row.get("opencode_session_id"))
        if not opencode_session_id:
            raise RuntimeError("OpenCode session is missing")
        model, _prompt_models = sc.resolve_model(
            session_row,
            request.get("provider") or "",
            request.get("model") or "",
            role,
            SURFACE_KOUBO_ASSET_AGENT_CHAT,
        sc=sc)
        client = sc.opencode_client_for(session_row, sc=sc)
        started_at = now_ms()
        client.prompt_async(
            opencode_session_id,
            _planner_user_prompt(request_id, task, request, sc=sc),
            model=model,
            system=PLANNER_SYSTEM_PROMPT,
            tools={"bash": False, "read": False, "write": False, "websearch": False},
        )
        deadline = time.time() + ASSET_SEARCH_PLANNER_TIMEOUT_SECONDS
        assistant_text = ""
        while time.time() < deadline:
            assistant_text = sc.last_completed_assistant(client.messages(opencode_session_id, limit=120), started_at) or ""
            if assistant_text:
                break
            time.sleep(1)
        if not assistant_text:
            raise TimeoutError("Run Model did not return an asset search plan")
        parsed = _extract_json_object(assistant_text)
        if not parsed:
            raise ValueError("Run Model returned non-JSON asset search plan")
        plan = normalize_planner_result(parsed, request, request_id)
        sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.plan.created", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "planner": "opencode",
            "query_count": len(plan.get("queries") or []),
            "media_types": plan.get("media_types") or [],
            "sources": plan.get("sources") or [],
        })
        return plan
    except Exception as exc:
        fallback["planner_error"] = _safe_error_detail(exc, sc=sc)
        sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.plan.created", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "planner": "fallback",
            "degraded": bool(fallback.get("degraded")),
            "detail": fallback["planner_error"],
        })
        return fallback


async def create_asset_search_plan(task: dict[str, Any], payload: dict[str, Any], role: str = AUTH_ROLE_USER, *, sc: Any) -> dict[str, Any]:
    return await asyncio.to_thread(_build_asset_search_plan_sync, task, payload or {}, role, sc=sc)


def asset_search_provider_ttl_ms(provider: str) -> int:
    return ASSET_SEARCH_PIXABAY_TTL_MS if _text(provider).lower() == "pixabay" else ASSET_SEARCH_DEFAULT_TTL_MS


def asset_search_cache_key(provider: str, normalized_query: str, media_type: str, aspect: str, page: int, language: str, safe_search: bool) -> str:
    value = "|".join([
        _text(provider).lower(),
        re.sub(r"\s+", " ", _text(normalized_query).lower()),
        _text(media_type).lower(),
        normalize_aspect(aspect),
        str(max(1, int(page or 1))),
        _text(language, "en").lower(),
        "true" if safe_search else "false",
    ])
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _cache_path(workspace: Path, provider: str, key: str) -> Path:
    return workspace / ASSET_SEARCH_CACHE_REL / _text(provider).lower() / f"{key}.json"


async def _cached_provider_response(
    workspace: Path,
    provider: Any,
    *,
    query: str,
    media_type: str,
    aspect: str,
    limit: int,
    page: int,
    language: str,
    safe_search: bool, sc: Any,
) -> tuple[dict[str, Any], bool]:
    key = asset_search_cache_key(provider.provider_id, query, media_type, aspect, page, language, safe_search)
    path = _cache_path(workspace, provider.provider_id, key)
    cached = sc.read_json(path)
    expires_at = _int(cached.get("expires_at"))
    if cached.get("provider") == provider.provider_id and expires_at and now_ms() < expires_at and isinstance(cached.get("response_raw"), dict):
        return cached["response_raw"], True
    response = await provider.search_raw(
        query=query,
        media_type=media_type,
        aspect=aspect,
        limit=limit,
        page=page,
        language=language,
        safe_search=safe_search,
    )
    cached_at = now_ms()
    sc.write_json(path, {
        "provider": provider.provider_id,
        "request": {
            "query": query,
            "media_type": media_type,
            "aspect": aspect,
            "page": page,
            "language": language,
            "safe_search": bool(safe_search),
        },
        "response_raw": response,
        "cached_at": cached_at,
        "expires_at": cached_at + asset_search_provider_ttl_ms(provider.provider_id),
    })
    return response, False


def _allowed_actions_for_source(
    provider_id: str, candidate_kind: str = ""
) -> list[str]:
    provider = _text(provider_id).lower()
    if provider == "media_library":
        if _text(candidate_kind) == "derived_clip":
            return ["preview", "import_clip"]
        return ["preview", "open_editor", "import_original"]
    if provider == "local":
        return ["preview", "reuse_local"]
    return ["preview", "import_whole"]


def _enforce_candidate_allowed_actions(
    candidate: dict[str, Any], provider_id: str | None = None
) -> dict[str, Any]:
    candidate["allowed_actions"] = _allowed_actions_for_source(
        provider_id or _text(candidate.get("provider")),
        _text(candidate.get("candidate_kind")),
    )
    return candidate


def normalize_asset_search_candidate(provider_id: str, raw: dict[str, Any], media_type: str, provider_rank: int = 1) -> dict[str, Any]:
    candidate = provider_for(provider_id).normalize(raw, media_type, provider_rank)
    return _enforce_candidate_allowed_actions(candidate, provider_id)


def _candidate_aspect_matches(candidate: dict[str, Any], aspect: str) -> bool:
    normalized = normalize_aspect(aspect)
    if normalized == "auto":
        return True
    orientation = _text(candidate.get("orientation"))
    expected = {
        "16:9": "landscape",
        "4:3": "landscape",
        "9:16": "portrait",
        "3:4": "portrait",
        "1:1": "square",
    }.get(normalized, "")
    return not expected or not orientation or orientation == "unknown" or orientation == expected


def _embedding_tokens(value: Any) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]{2,}", _text(value).lower())
        if token not in {"the", "and", "with", "from", "that", "this", "into", "asset", "media"}
    ][:120]


def _embedding_vector(value: Any) -> dict[str, float]:
    vector: dict[str, float] = {}
    for token in _embedding_tokens(value):
        vector[token] = vector.get(token, 0.0) + 1.0
    return vector


def _cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0.0) for key, value in left.items())
    left_norm = sum(value * value for value in left.values()) ** 0.5
    right_norm = sum(value * value for value in right.values()) ** 0.5
    return 0.0 if not left_norm or not right_norm else max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _candidate_embedding_text(candidate: dict[str, Any]) -> str:
    return " ".join([
        _text(candidate.get("title")),
        _text(candidate.get("description")),
        _text(candidate.get("query")),
        " ".join(_text(tag) for tag in candidate.get("tags") or []),
        _text((candidate.get("storyboard_ref") or {}).get("label") if isinstance(candidate.get("storyboard_ref"), dict) else ""),
    ])


def _plan_embedding_text(plan: dict[str, Any]) -> str:
    return " ".join([
        _text(plan.get("summary")),
        " ".join(_text(item.get("query")) for item in plan.get("queries") or [] if isinstance(item, dict)),
        " ".join(_text(item) for item in plan.get("must_have") or []),
        " ".join(_text(item) for item in plan.get("nice_to_have") or []),
        _text(plan.get("style")),
    ])


def rerank_asset_search_candidates(candidates: list[dict[str, Any]], plan: dict[str, Any], settings: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not ((settings.get("ranking") or {}).get("embedding_rerank") is not False):
        return candidates, {"enabled": False, "method": "disabled"}
    query_vector = _embedding_vector(_plan_embedding_text(plan))
    if not query_vector:
        return candidates, {"enabled": False, "method": "text_embedding", "reason": "empty_query_vector"}
    reranked: list[dict[str, Any]] = []
    for candidate in candidates:
        similarity = _cosine_similarity(query_vector, _embedding_vector(_candidate_embedding_text(candidate)))
        base_score = max(0.0, min(1.0, float(candidate.get("score") or 0.0)))
        score = round(max(0.0, min(1.0, base_score * 0.82 + similarity * 0.18)), 4)
        reasons = [*([_text(item) for item in candidate.get("score_reasons") or [] if _text(item)])]
        if similarity:
            reasons.append("text embedding rerank")
        reranked.append({**candidate, "embedding_score": round(similarity, 4), "score": score, "score_reasons": reasons[:8]})
    reranked.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return reranked, {"enabled": True, "method": "text_embedding", "query_terms": len(query_vector)}


def _dedupe_text_items(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = re.sub(r"\s+", " ", _text(item).strip())
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _wikimedia_query_variants(query: str, media_type: str) -> list[str]:
    raw = _text(query)
    if not raw:
        return []
    lowered = raw.lower().replace("b-roll", "broll")
    tokens = [
        token
        for token in re.findall(r"[a-z][a-z0-9_-]*", lowered)
        if token not in WIKIMEDIA_QUERY_NOISE_TERMS and len(token) > 2
    ]
    variants = [raw]
    cleaned = " ".join(tokens[:8])
    if cleaned and cleaned.lower() != raw.lower():
        variants.append(cleaned)
    token_set = set(tokens)
    phrase_rules = [
        (("hospital", "corridor"), "hospital corridor"),
        (("hospital", "hallway"), "hospital hallway"),
        (("medical", "staff"), "medical staff"),
        (("doctor", "hospital"), "doctor hospital"),
        (("nurse", "hospital"), "nurse hospital"),
        (("tablet", "hospital"), "tablet hospital"),
    ]
    for required, phrase in phrase_rules:
        if all(item in token_set for item in required):
            variants.append(phrase)
    if len(tokens) >= 2:
        variants.append(" ".join(tokens[:3]))
    if media_type == "video" and "hospital" in token_set:
        variants.append("hospital")
    return _dedupe_text_items(variants, 5)


def asset_search_provider_query_variants(provider_id: str, query: str, media_type: str) -> list[str]:
    if _text(provider_id).lower() != "wikimedia":
        return [_text(query)] if _text(query) else []
    return _wikimedia_query_variants(query, media_type)


def _media_library_orientation(aspect: str) -> str:
    return {
        "16:9": "landscape",
        "4:3": "landscape",
        "9:16": "portrait",
        "3:4": "portrait",
    }.get(normalize_aspect(aspect), "any")


def _model_payload(value: Any) -> dict[str, Any]:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
        return payload if isinstance(payload, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


class MediaLibraryProviderAdapter:
    """Adapts the shared global media-library search to the Agent candidate shell."""

    provider_id = "media_library"

    def __init__(self, search_service: Any) -> None:
        self.search_service = search_service

    @staticmethod
    def _query_text(plan: dict[str, Any], request: dict[str, Any]) -> str:
        # The Agent planner translates queries for stock providers. The global
        # library indexes the original dialogue, so preserve the user's source
        # language whenever it is available.
        query = _text(request.get("user_text"))
        if query:
            return query
        query = _text(plan.get("summary"))
        if query:
            return query
        return " ".join(
            _text(item.get("query"))
            for item in plan.get("queries") or []
            if isinstance(item, dict) and _text(item.get("query"))
        )[:1000]

    @staticmethod
    def _candidate(
        value: Any, *, shared_search_id: str, provider_rank: int
    ) -> dict[str, Any]:
        item = _model_payload(value)
        candidate_kind = _text(
            item.get("candidate_kind"), "original_video"
        )
        candidate_id = _text(item.get("candidate_id"))
        asset_id = _text(item.get("asset_id"))
        source_asset_id = _text(
            item.get("source_asset_id"), asset_id
        )
        matched_fragments = [
            _model_payload(fragment)
            for fragment in item.get("matched_fragments") or []
        ]
        description = next(
            (
                _text(fragment.get("dialogue_text") or fragment.get("summary"))
                for fragment in matched_fragments
                if _text(
                    fragment.get("dialogue_text") or fragment.get("summary")
                )
            ),
            "",
        )
        duration_ms = item.get("duration_ms")
        return _enforce_candidate_allowed_actions(
            {
                "schema_version": ASSET_SEARCH_CANDIDATE_SCHEMA,
                "candidate_id": candidate_id,
                "candidate_kind": candidate_kind,
                "provider": "media_library",
                "source": "media_library",
                "provider_asset_id": candidate_id,
                "asset_id": asset_id or None,
                "source_asset_id": source_asset_id,
                "source_clip_id": item.get("source_clip_id"),
                "source_version": _text(item.get("source_version")),
                "content_sha256": _text(
                    item.get("content_sha256")
                    or item.get("source_version")
                ),
                "media_type": "video",
                "title": _text(item.get("display_name"), candidate_id),
                "description": description,
                "preview_url": _text(item.get("preview_url")),
                "thumbnail_url": _text(item.get("thumbnail_url")),
                # Global media is copied from its authoritative workspace by
                # MediaLibraryStoryBoardImportService. It never exposes a
                # download URL to the external-provider import branch.
                "download_url": "",
                "source_url": "",
                "creator": {"name": "OpenCrew 全局素材库", "url": ""},
                "license": {
                    "name": "OpenCrew 全局素材库",
                    "url": "",
                    "requires_attribution": False,
                    "attribution_text": "",
                    "license_status": "confirmed",
                },
                "width": 0,
                "height": 0,
                "duration_seconds": (
                    float(duration_ms) / 1000
                    if duration_ms is not None
                    else None
                ),
                "duration_ms": duration_ms,
                "mime_type": "video/mp4",
                "orientation": _text(item.get("orientation"), "any"),
                "tags": [
                    _text(tag)
                    for tag in item.get("tags") or []
                    if _text(tag)
                ],
                "provider_rank": provider_rank,
                "import_supported": True,
                "import_unsupported_reason": "",
                "score": float(item.get("score") or 0),
                "raw_score": float(item.get("raw_score") or 0),
                "score_reasons": [
                    _text(reason)
                    for reason in item.get("score_reasons") or []
                    if _text(reason)
                ],
                "local_reuse": False,
                "global_media_library": True,
                "matched_fragments": matched_fragments,
                "search_eligible_source": candidate_kind,
                "media_library_search_id": shared_search_id,
                "raw": {},
            },
            "media_library",
        )

    async def search(
        self,
        *,
        task: dict[str, Any],
        plan: dict[str, Any],
        request: dict[str, Any],
        limit: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if "video" not in request.get("media_types", []):
            return [], {
                "requested": 0,
                "returned": 0,
                "kept": 0,
                "status": "ok",
                "cache_hits": 0,
            }
        query = self._query_text(plan, request)
        response = await self.search_service.search(
            {
                "query": query,
                "user_query": query,
                "entry_point": "agent",
                "query_source": "manual",
                "target_task_id": int(task["id"]),
                "orientation": _media_library_orientation(
                    _text(request.get("aspect"), "auto")
                ),
                "sources": ["media_library"],
                "limit": limit,
                "offset": 0,
            }
        )
        response_payload = _model_payload(response)
        shared_search_id = _text(
            response_payload.get("search_id")
            or getattr(response, "search_id", "")
        )
        raw_items = (
            response_payload.get("items")
            if isinstance(response_payload.get("items"), list)
            else list(getattr(response, "items", []) or [])
        )
        candidates = [
            self._candidate(
                item,
                shared_search_id=shared_search_id,
                provider_rank=index,
            )
            for index, item in enumerate(raw_items[:limit], start=1)
        ]
        total_count = _int(
            response_payload.get("total_count")
            or getattr(response, "total_count", len(candidates)),
            len(candidates),
        )
        return candidates, {
            "requested": 1,
            "returned": total_count,
            "kept": len(candidates),
            "status": "ok",
            "cache_hits": 0,
            "search_id": shared_search_id,
        }


async def _search_media_library_candidates(
    task: dict[str, Any],
    plan: dict[str, Any],
    request: dict[str, Any],
    settings: dict[str, Any],
    *,
    sc: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_settings = (
        (settings.get("sources") or {}).get("media_library")
        if isinstance(settings.get("sources"), dict)
        else {}
    )
    limit = max(
        1,
        min(
            _int(
                (source_settings or {}).get("limit_per_query"),
                request.get("limit_per_source") or 12,
            ),
            24,
        ),
    )
    context = getattr(sc, "ctx", None)
    service = getattr(context, "media_library_search_service", None)
    if service is None:
        raise RuntimeError("media_library_search_service_unavailable")
    return await MediaLibraryProviderAdapter(service).search(
        task=task,
        plan=plan,
        request=request,
        limit=limit,
    )


async def _search_provider_candidates(
    workspace: Path,
    provider_id: str,
    plan: dict[str, Any],
    request: dict[str, Any],
    settings: dict[str, Any], *, sc: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    credentials = _provider_credentials(sc=sc)
    source_settings = (settings.get("sources") or {}).get(provider_id) if isinstance(settings.get("sources"), dict) else {}
    limit = max(1, min(_int((source_settings or {}).get("limit_per_query"), request.get("limit_per_source") or 12), 24))
    stats = {"requested": 0, "returned": 0, "kept": 0, "status": "ok", "cache_hits": 0}
    if provider_id in ASSET_SEARCH_KEYED_PROVIDERS and not credentials.get(provider_id):
        stats["status"] = "error"
        stats["detail"] = "provider_not_configured"
        return [], stats
    provider = provider_for(provider_id, credentials.get(provider_id, ""))
    candidates: list[dict[str, Any]] = []
    for query in plan.get("queries") or []:
        if not isinstance(query, dict):
            continue
        media_type = _text(query.get("media_type")).lower()
        if media_type not in request["media_types"] or media_type not in ASSET_SEARCH_MEDIA_TYPES:
            continue
        original_query = _text(query.get("query"))
        base_queries = [_text(item) for item in query.get("query_variants") or [] if _text(item)]
        base_queries.append(original_query)
        for base_query in _dedupe_query_variants(base_queries, 3):
            before_base_count = len(candidates)
            for query_text in asset_search_provider_query_variants(provider_id, base_query, media_type):
                query_language = "zh" if _contains_cjk(query_text) else _text(query.get("language"), request["language"])
                if not re.fullmatch(r"[a-z]{2}", query_language):
                    query_language = request["language"]
                stats["requested"] += 1
                before_count = len(candidates)
                try:
                    response, cache_hit = await _cached_provider_response(
                        workspace,
                        provider,
                        query=query_text,
                        media_type=media_type,
                        aspect=request["aspect"],
                        limit=limit,
                        page=1,
                        language=query_language,
                        safe_search=bool(request["safe_search"]),
                    sc=sc)
                    if cache_hit:
                        stats["cache_hits"] += 1
                    if query_text != original_query:
                        stats.setdefault("fallback_queries", []).append(query_text)
                    items = provider.response_items(response, media_type)
                    stats["returned"] += len(items)
                    for index, item in enumerate(items, start=1):
                        candidate = provider.normalize(item, media_type, index)
                        _enforce_candidate_allowed_actions(
                            candidate, provider_id
                        )
                        candidate["query"] = query_text
                        if query_text != original_query:
                            candidate["original_query"] = original_query
                            reasons = [_text(item) for item in candidate.get("score_reasons") or [] if _text(item)]
                            candidate["score_reasons"] = [*reasons, "provider-specific fallback query"][:8]
                        storyboard_ref = query.get("storyboard_ref") if isinstance(query.get("storyboard_ref"), dict) else {}
                        if storyboard_ref:
                            candidate["storyboard_ref"] = storyboard_ref
                        if candidate.get("media_type") not in request["media_types"]:
                            continue
                        if not _candidate_aspect_matches(candidate, request["aspect"]):
                            continue
                        candidates.append(candidate)
                    if provider_id == "wikimedia" and len(candidates) > before_count:
                        break
                except AssetSearchRateLimit:
                    stats["status"] = "rate_limited"
                    break
                except Exception as exc:
                    stats["status"] = "error"
                    stats["detail"] = _safe_error_detail(exc, sc=sc)
                    break
            if stats["status"] in {"rate_limited", "error"}:
                break
            if provider_id == "wikimedia" and len(candidates) > before_base_count:
                break
        if stats["status"] in {"rate_limited", "error"}:
            break
    stats["kept"] = len(candidates)
    return candidates, stats


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        keys = [
            _text(candidate.get("candidate_id")),
            "|".join([_text(candidate.get("provider")), _text(candidate.get("media_type")), _text(candidate.get("provider_asset_id"))]),
            _text(candidate.get("source_url")),
            _text(candidate.get("preview_url")),
        ]
        key = next((item for item in keys if item), "")
        if key and key in seen:
            continue
        for item in keys:
            if item:
                seen.add(item)
        deduped.append(candidate)
    deduped.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return deduped


def _asset_kind_for_local_asset(asset: dict[str, Any]) -> str:
    explicit = _text(asset.get("kind") or asset.get("asset_type")).lower()
    if "video" in explicit:
        return "video"
    if "audio" in explicit:
        return "audio"
    if "image" in explicit:
        return "image"
    suffix = Path(_text(asset.get("path") or asset.get("filename"))).suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in AUDIO_EXTS:
        return "audio"
    if suffix in IMAGE_EXTS:
        return "image"
    return ""


def _local_asset_preview_url(session_id: int, rel_path: str) -> str:
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in _text(rel_path).split("/") if part)
    return f"/api/session-tasks/{int(session_id)}/raw/{encoded}" if encoded else ""


def _local_asset_embedding_text(asset: dict[str, Any]) -> str:
    origin = asset.get("origin") if isinstance(asset.get("origin"), dict) else {}
    storyboard_ref = origin.get("storyboard_ref") if isinstance(origin.get("storyboard_ref"), dict) else {}
    return " ".join([
        _text(asset.get("label")),
        _text(asset.get("filename")),
        _text(asset.get("path")),
        _text(asset.get("source")),
        _text(origin.get("tool")),
        _text(origin.get("provider")),
        _text(origin.get("model")),
        _text(origin.get("prompt")),
        _text(origin.get("query")),
        _text(storyboard_ref.get("label")),
        " ".join(_text(tag) for tag in asset.get("tags") or []),
    ])


def _local_asset_candidate(task: dict[str, Any], asset: dict[str, Any], media_type: str, similarity: float, provider_rank: int) -> dict[str, Any]:
    rel_path = _text(asset.get("path") or asset.get("id"))
    filename = _text(asset.get("filename"), Path(rel_path).name)
    digest = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]
    origin = asset.get("origin") if isinstance(asset.get("origin"), dict) else {}
    origin_license = origin.get("license") if isinstance(origin.get("license"), dict) else {}
    source_url = _text(origin.get("source_url")) or _local_asset_preview_url(int(task["session_id"]), rel_path)
    score = round(max(0.0, min(1.0, 0.56 + similarity * 0.4)), 4)
    reasons = ["local Asset Library match", "already in current task"]
    if similarity:
        reasons.append(f"text similarity {round(similarity, 3)}")
    return {
        "schema_version": ASSET_SEARCH_CANDIDATE_SCHEMA,
        "candidate_id": f"local_{media_type}_{digest}",
        "provider": "local",
        "provider_asset_id": digest,
        "media_type": media_type,
        "title": _text(asset.get("label") or filename, filename),
        "description": _text(origin.get("prompt") or origin.get("query") or asset.get("source")),
        "preview_url": _local_asset_preview_url(int(task["session_id"]), rel_path),
        "thumbnail_url": _local_asset_preview_url(int(task["session_id"]), rel_path) if media_type == "image" else "",
        "download_url": _local_asset_preview_url(int(task["session_id"]), rel_path),
        "source_url": source_url,
        "creator": origin.get("creator") if isinstance(origin.get("creator"), dict) else {"name": "Current Asset Library", "url": ""},
        "license": origin_license or {
            "name": "Local Asset Library",
            "url": "",
            "requires_attribution": False,
            "attribution_text": "",
            "license_status": "confirmed",
        },
        "width": _int(asset.get("width") or origin.get("width")),
        "height": _int(asset.get("height") or origin.get("height")),
        "duration_seconds": asset.get("duration_seconds") or origin.get("duration_seconds"),
        "mime_type": mimetypes.guess_type(rel_path)[0] or ("video/mp4" if media_type == "video" else "audio/mpeg" if media_type == "audio" else "image/jpeg"),
        "orientation": asset_search_providers.orientation_for(asset.get("width") or origin.get("width"), asset.get("height") or origin.get("height")),
        "tags": [_text(tag) for tag in asset.get("tags") or [] if _text(tag)][:16],
        "provider_rank": provider_rank,
        "import_supported": True,
        "import_unsupported_reason": "",
        "allowed_actions": _allowed_actions_for_source("local"),
        "score": score,
        "score_reasons": reasons,
        "local_reuse": True,
        "local_asset_path": rel_path,
        "raw": {"asset": asset},
    }


def _search_local_asset_candidates(
    workspace: Path,
    task: dict[str, Any],
    plan: dict[str, Any],
    request: dict[str, Any],
    settings: dict[str, Any], *, sc: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_settings = (settings.get("sources") or {}).get("local") if isinstance(settings.get("sources"), dict) else {}
    limit = max(1, min(_int((source_settings or {}).get("limit_per_query"), request.get("limit_per_source") or 12), 24))
    query_vector = _embedding_vector(_plan_embedding_text(plan) or request.get("user_text"))
    candidates: list[dict[str, Any]] = []
    returned = 0
    for asset in _manifest_assets(workspace, sc=sc):
        rel_path = _text(asset.get("path") or asset.get("id"))
        media_type = _asset_kind_for_local_asset(asset)
        if not rel_path or media_type not in request["media_types"]:
            continue
        if not rel_path.startswith((f"{ASSET_IMAGES_REL}/", f"{ASSET_VIDEOS_REL}/", f"{ASSET_AUDIOS_REL}/")):
            continue
        try:
            _rel, abs_path = sc.safe_workspace_rel(workspace, rel_path)
        except Exception:
            continue
        if not abs_path.exists() or not abs_path.is_file():
            continue
        returned += 1
        similarity = _cosine_similarity(query_vector, _embedding_vector(_local_asset_embedding_text(asset))) if query_vector else 0.0
        if query_vector and similarity <= 0:
            continue
        candidate = _local_asset_candidate(task, asset, media_type, similarity, len(candidates) + 1)
        if not _candidate_aspect_matches(candidate, request["aspect"]):
            continue
        candidates.append(candidate)
    candidates.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    for index, candidate in enumerate(candidates[:limit], start=1):
        candidate["provider_rank"] = index
    kept = candidates[:limit]
    return kept, {"requested": 1, "returned": returned, "kept": len(kept), "status": "ok", "cache_hits": 0}


def _search_run_path(workspace: Path, search_id: str) -> Path:
    safe_id = _text(search_id)
    if not re.fullmatch(r"search_\d+(?:_[a-z0-9]+)?", safe_id):
        raise HTTPException(status_code=404, detail="Asset search run not found")
    return workspace / ASSET_SEARCH_RUNS_REL / f"{safe_id}.json"


def write_asset_search_run(workspace: Path, run: dict[str, Any], *, sc: Any) -> None:
    sc.write_json(_search_run_path(workspace, _text(run.get("search_id"))), run)


def list_asset_search_runs(task: dict[str, Any], *, sc: Any) -> list[dict[str, Any]]:
    root = sc.workspace_for(task) / ASSET_SEARCH_RUNS_REL
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("search_*.json"), reverse=True)[:50]:
        payload = sc.read_json(path)
        if payload:
            items.append({
                "search_id": _text(payload.get("search_id")),
                "created_at": _int(payload.get("created_at")),
                "request": payload.get("request") or {},
                "plan": payload.get("plan") or {},
                "candidate_count": len(payload.get("candidates") or []),
                "provider_stats": payload.get("provider_stats") or {},
            })
    return items


def load_asset_search_run(task: dict[str, Any], search_id: str, *, sc: Any) -> dict[str, Any]:
    payload = sc.read_json(_search_run_path(sc.workspace_for(task), search_id))
    if not payload:
        raise HTTPException(status_code=404, detail="Asset search run not found")
    return payload


async def _run_asset_search_provider(
    workspace: Path,
    task: dict[str, Any],
    provider_id: str,
    plan: dict[str, Any],
    request: dict[str, Any],
    settings: dict[str, Any], *, sc: Any,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    try:
        if provider_id == "local":
            candidates, stats = await asyncio.to_thread(_search_local_asset_candidates, workspace, task, plan, request, settings, sc=sc)
        elif provider_id == "media_library":
            candidates, stats = await _search_media_library_candidates(
                task, plan, request, settings, sc=sc
            )
        else:
            candidates, stats = await _search_provider_candidates(workspace, provider_id, plan, request, settings, sc=sc)
        return provider_id, candidates, stats
    except Exception as exc:
        return provider_id, [], {"requested": 0, "returned": 0, "kept": 0, "status": "error", "cache_hits": 0, "detail": _safe_error_detail(exc, sc=sc)}


async def stream_asset_search_events(task: dict[str, Any], payload: dict[str, Any], role: str = AUTH_ROLE_USER, *, sc: Any) -> AsyncIterator[dict[str, Any]]:
    workspace = sc.workspace_for(task)
    settings = read_asset_search_settings(task, sc=sc)
    request = normalize_asset_search_request(payload or {}, settings)
    search_id = f"search_{now_ms()}_{uuid.uuid4().hex[:6]}"
    created_at = now_ms()
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.started", {
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "search_id": search_id,
        "providers": request["sources"],
    })
    yield {"type": "started", "search_id": search_id}
    try:
        supplied_plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else None
        if supplied_plan and supplied_plan.get("queries"):
            plan = normalize_supplied_asset_search_plan(supplied_plan, request)
            sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.plan.created", {
                "task_id": int(task["id"]),
                "session_id": int(task["session_id"]),
                "planner": "user_edited",
                "query_count": len(plan.get("queries") or []),
                "media_types": plan.get("media_types") or [],
                "sources": plan.get("sources") or [],
            })
        else:
            plan = await create_asset_search_plan(task, {**payload, **request}, role, sc=sc)
        plan["sources"] = normalize_sources(plan.get("sources") or request["sources"], settings)
        plan["media_types"] = normalize_media_types(plan.get("media_types"), request["media_types"])
        request["aspect"] = normalize_aspect(plan.get("aspect"), request["aspect"])
        yield {"type": "plan", "plan": plan}
        provider_stats: dict[str, Any] = {}
        candidates: list[dict[str, Any]] = []
        provider_tasks: list[asyncio.Task[tuple[str, list[dict[str, Any]], dict[str, Any]]]] = []
        for provider_id in plan["sources"]:
            yield {"type": "provider.started", "provider": provider_id}
            sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.provider.started", {
                "task_id": int(task["id"]),
                "session_id": int(task["session_id"]),
                "search_id": search_id,
                "provider": provider_id,
            })
            provider_tasks.append(asyncio.create_task(_run_asset_search_provider(workspace, task, provider_id, plan, request, settings, sc=sc)))
        for provider_task in asyncio.as_completed(provider_tasks):
            provider_id, provider_candidates, stats = await provider_task
            provider_stats[provider_id] = stats
            if provider_candidates:
                yield {"type": "candidate.batch", "provider": provider_id, "items": provider_candidates}
            yield {"type": "provider.completed", "provider": provider_id, "returned": stats.get("returned", 0), "kept": stats.get("kept", 0), "status": stats.get("status", "ok")}
            sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.provider.completed", {
                "task_id": int(task["id"]),
                "session_id": int(task["session_id"]),
                "search_id": search_id,
                "provider": provider_id,
                "returned": stats.get("returned", 0),
                "kept": stats.get("kept", 0),
                "status": stats.get("status", "ok"),
            })
            candidates.extend(provider_candidates)
        candidates, ranking_meta = rerank_asset_search_candidates(_dedupe_candidates(candidates), plan, settings)
        candidates = candidates[: max(1, min(_int((settings.get("defaults") or {}).get("max_candidates"), 36), 72))]
        run = {
            "schema_version": ASSET_SEARCH_RUN_SCHEMA,
            "search_id": search_id,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "created_at": created_at,
            "request": {
                "user_text": request["user_text"],
                "media_types": request["media_types"],
                "aspect": request["aspect"],
                "sources": plan["sources"],
                "limit_per_source": request["limit_per_source"],
            },
            "plan": plan,
            "candidates": candidates,
            "provider_stats": provider_stats,
            "ranking": ranking_meta,
        }
        write_asset_search_run(workspace, run, sc=sc)
        sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.completed", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "search_id": search_id,
            "candidate_count": len(candidates),
            "providers": plan["sources"],
            "ranking": ranking_meta,
        })
        yield {"type": "completed", "search_id": search_id, "candidate_count": len(candidates), "items": candidates, "provider_stats": provider_stats, "ranking": ranking_meta}
    except Exception as exc:
        sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.failed", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "search_id": search_id,
            "detail": _text(exc)[:500],
        })
        yield {"type": "failed", "detail": _text(exc) or "Asset search failed"}


def _manifest_assets(workspace: Path, *, sc: Any) -> list[dict[str, Any]]:
    payload = sc.read_json(workspace / ASSETS_REL)
    return [item for item in payload.get("assets") or [] if isinstance(item, dict)]


def asset_search_candidate_identity(candidate: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _text(candidate.get("candidate_id")),
        _text(candidate.get("provider")).lower(),
        _text(candidate.get("media_type")).lower(),
        _text(candidate.get("provider_asset_id")),
    )


def asset_search_manifest_existing_candidate(workspace: Path, candidate: dict[str, Any], *, sc: Any) -> dict[str, Any] | None:
    candidate_id, provider, media_type, provider_asset_id = asset_search_candidate_identity(candidate)
    for item in _manifest_assets(workspace, sc=sc):
        origin = item.get("origin") if isinstance(item.get("origin"), dict) else {}
        if candidate_id and _text(origin.get("candidate_id")) == candidate_id:
            return item
        if (
            provider
            and provider == _text(origin.get("provider")).lower()
            and media_type == _text(origin.get("media_type")).lower()
            and provider_asset_id == _text(origin.get("provider_asset_id"))
        ):
            return item
    return None


def asset_search_manifest_existing_local_candidate(workspace: Path, candidate: dict[str, Any], *, sc: Any) -> dict[str, Any] | None:
    local_path = _text(candidate.get("local_asset_path"))
    local_id = _text(candidate.get("local_asset_id") or candidate.get("provider_asset_id"))
    for item in _manifest_assets(workspace, sc=sc):
        if local_path and (_text(item.get("path")) == local_path or _text(item.get("id")) == local_path):
            return item
        if local_id and (_text(item.get("id")) == local_id or _text(item.get("path")) == local_id):
            return item
    return None


def _manifest_existing_hash(workspace: Path, content_sha256: str, *, sc: Any) -> dict[str, Any] | None:
    digest = _text(content_sha256)
    if not digest:
        return None
    for item in _manifest_assets(workspace, sc=sc):
        if _text(item.get("content_sha256")) == digest:
            return item
    return None


def _license_import_error(candidate: dict[str, Any], confirm_license: bool = False) -> str:
    license_info = candidate.get("license") if isinstance(candidate.get("license"), dict) else {}
    if license_info.get("requires_attribution") and not _text(license_info.get("attribution_text")):
        return "attribution_required_but_missing"
    if license_info.get("license_status") == "unconfirmed" and not confirm_license:
        return "license_unconfirmed"
    return ""


def _asset_kind_payload(media_type: str) -> tuple[str, str, str, set[str]]:
    kind = _text(media_type).lower()
    if kind == "video":
        return ASSET_VIDEOS_REL, "Video", "video", VIDEO_EXTS
    if kind == "audio":
        return ASSET_AUDIOS_REL, "Audio", "audio", AUDIO_EXTS
    return ASSET_IMAGES_REL, "Image", "image", IMAGE_EXTS


def _extension_media_type(suffix: str) -> str:
    ext = _text(suffix).lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return ""


def _extension_for_candidate(candidate: dict[str, Any]) -> str:
    media_type = _text(candidate.get("media_type"), "image")
    _root, _asset_type, _kind, allowed = _asset_kind_payload(media_type)
    mime = _text(candidate.get("mime_type")).lower()
    guessed = mimetypes.guess_extension(mime.split(";", 1)[0]) if mime else ""
    suffix = _text(guessed).lower()
    if suffix == ".jpe":
        suffix = ".jpg"
    url_suffix = Path(urllib_parse_path(candidate.get("download_url"))).suffix.lower()
    if suffix in allowed:
        return suffix
    if url_suffix in allowed:
        return url_suffix
    return ".mp4" if media_type == "video" else ".mp3" if media_type == "audio" else ".jpg"


def urllib_parse_path(url: Any) -> str:
    try:
        return urllib.parse.urlparse(_text(url)).path
    except Exception:
        return ""


def _slug(value: Any, fallback: str = "asset") -> str:
    raw = _text(value).lower()
    raw = re.sub(r"[^a-z0-9\u3400-\u9fff]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw[:48] or fallback


def asset_search_target_rel_path(candidate: dict[str, Any], index: int, label_prefix: str = "") -> str:
    root, _asset_type, _kind, _allowed = _asset_kind_payload(_text(candidate.get("media_type"), "image"))
    provider = re.sub(r"[^a-z0-9_-]+", "_", _text(candidate.get("provider")).lower()) or "provider"
    provider_asset_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", _text(candidate.get("provider_asset_id")))[:48] or "asset"
    slug = _slug(label_prefix or candidate.get("title") or candidate.get("query"), "asset")
    ext = _extension_for_candidate(candidate)
    return f"{root}/{now_ms()}_{max(1, index):03d}_{provider}_{provider_asset_id}_{slug}{ext}"


def asset_search_validate_import_prefix(rel_path: str, media_type: str) -> None:
    root, _asset_type, _kind, _allowed = _asset_kind_payload(media_type)
    rel = _text(rel_path)
    if not rel.startswith(f"{root}/"):
        raise HTTPException(status_code=400, detail="Asset search import target must stay inside the media asset directory")


def _unique_target_path(workspace: Path, rel_path: str, media_type: str, *, sc: Any) -> tuple[str, Path]:
    asset_search_validate_import_prefix(rel_path, media_type)
    rel, target = sc.safe_workspace_rel(workspace, rel_path)
    asset_search_validate_import_prefix(rel, media_type)
    if not target.exists():
        return rel, target
    for index in range(2, 1000):
        candidate = target.with_name(f"{target.stem}_{index}{target.suffix}")
        rel_candidate = candidate.relative_to(workspace).as_posix()
        asset_search_validate_import_prefix(rel_candidate, media_type)
        if not candidate.exists():
            return rel_candidate, candidate
    raise HTTPException(status_code=409, detail="Could not choose a unique asset search import filename")


def _header_media_type(path: Path) -> str:
    data = path.read_bytes()[:32]
    if data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"GIF8") or data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image"
    if len(data) >= 12 and data[4:8] == b"ftyp" or data.startswith(b"\x1aE\xdf\xa3"):
        return "video"
    if data.startswith(b"ID3") or data.startswith(b"OggS") or data.startswith(b"RIFF") and data[8:12] == b"WAVE" or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "audio"
    return ""


def _validate_downloaded_file(path: Path, media_type: str, candidate_mime: str, response_mime: str, max_bytes: int, expected_suffix: str = "") -> None:
    if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        raise AssetSearchProviderError("downloaded file is empty")
    if path.stat().st_size > max_bytes:
        raise AssetSearchProviderError("downloaded file exceeds size limit")
    expected = _text(media_type).lower()
    suffix_media_type = _extension_media_type(expected_suffix)
    if expected_suffix and not suffix_media_type:
        raise AssetSearchProviderError("downloaded file extension is not supported")
    if suffix_media_type and suffix_media_type != expected:
        raise AssetSearchProviderError("downloaded file extension does not match candidate media_type")
    for mime in (_text(candidate_mime).lower(), _text(response_mime).lower()):
        if mime and "/" in mime:
            top = mime.split("/", 1)[0]
            if top in {"image", "video", "audio"} and top != expected:
                raise AssetSearchProviderError("downloaded file MIME type does not match candidate media_type")
    detected = _header_media_type(path)
    if detected and detected != expected:
        raise AssetSearchProviderError("downloaded file header does not match candidate media_type")
    if detected and suffix_media_type and suffix_media_type != detected:
        raise AssetSearchProviderError("downloaded file header does not match target extension")


async def _refresh_stale_candidate(candidate: dict[str, Any], run: dict[str, Any], *, sc: Any) -> tuple[dict[str, Any] | None, str]:
    provider_id = _text(candidate.get("provider")).lower()
    created_at = _int(run.get("created_at"))
    if created_at and now_ms() - created_at <= asset_search_provider_ttl_ms(provider_id):
        return candidate, ""
    try:
        provider = provider_for(provider_id, _provider_credentials(sc=sc).get(provider_id, ""))
        refreshed = await provider.refresh_candidate(candidate)
        if not refreshed or not _text(refreshed.get("download_url")):
            return None, "stale_candidate_url"
        refreshed["candidate_id"] = _text(candidate.get("candidate_id")) or _text(refreshed.get("candidate_id"))
        refreshed["query"] = _text(candidate.get("query") or refreshed.get("query"))
        return refreshed, ""
    except Exception:
        return None, "stale_candidate_url"


def _candidate_by_id(run: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    for candidate in run.get("candidates") or []:
        if isinstance(candidate, dict) and _text(candidate.get("candidate_id")) == candidate_id:
            return candidate
    return None


async def _import_media_library_candidate(
    task: dict[str, Any],
    *,
    agent_search_id: str,
    candidate: dict[str, Any],
    label_prefix: str,
    dialogue_asset_key: str,
    sc: Any,
) -> dict[str, Any]:
    candidate_kind = _text(
        candidate.get("candidate_kind"), "original_video"
    )
    expected_actions = _allowed_actions_for_source(
        "media_library", candidate_kind
    )
    if candidate.get("allowed_actions") != expected_actions:
        raise RuntimeError("candidate_action_not_allowed")
    candidate_id = _text(candidate.get("candidate_id"))
    provider_asset_id = _text(candidate.get("provider_asset_id"))
    asset_id = _text(candidate.get("asset_id"))
    source_asset_id = _text(candidate.get("source_asset_id"))
    source_clip_id = _text(candidate.get("source_clip_id"))
    if candidate_kind == "original_video" and not source_asset_id:
        source_asset_id = asset_id
    if candidate_kind == "derived_clip":
        from opcrew_backend.media_library_features import (
            require_media_library_feature,
        )

        require_media_library_feature("clip_search_v1")
        if (
            not candidate_id
            or provider_asset_id != candidate_id
            or source_clip_id != candidate_id
            or asset_id
            or not source_asset_id
        ):
            raise RuntimeError("media_library_candidate_identity_invalid")
    elif (
        not asset_id
        or provider_asset_id != asset_id
        or candidate_id != asset_id
        or source_asset_id != asset_id
        or source_clip_id
    ):
        raise RuntimeError("media_library_candidate_identity_invalid")
    shared_search_id = _text(candidate.get("media_library_search_id"))
    if not shared_search_id:
        raise RuntimeError("media_library_search_id_missing")
    context = getattr(sc, "ctx", None)
    import_service = getattr(context, "media_library_import_service", None)
    if import_service is None:
        raise RuntimeError("media_library_import_service_unavailable")

    # Agent import retries are idempotent even though its legacy request shell
    # predates a public idempotency-key field. The authoritative import service
    # still receives a stable, bounded key.
    idempotency_digest = hashlib.sha256(
        "|".join(
            [
                str(int(task["id"])),
                agent_search_id,
                _text(candidate.get("candidate_id")),
                shared_search_id,
            ]
        ).encode("utf-8")
    ).hexdigest()
    from opcrew_backend.media_library_imports.schemas import (
        StoryBoardImportRequest,
    )

    request = StoryBoardImportRequest(
        target_task_id=int(task["id"]),
        requested_name=label_prefix or _text(candidate.get("title")) or None,
        search_id=shared_search_id,
        dialogue_asset_key=dialogue_asset_key or None,
        idempotency_key=f"agent-asset-search:{idempotency_digest}",
    )
    result = await asyncio.to_thread(
        (
            import_service.import_clip
            if candidate_kind == "derived_clip"
            else import_service.import_original
        ),
        source_asset_id,
        *(
            [source_clip_id, request]
            if candidate_kind == "derived_clip"
            else [request]
        ),
        **(
            {"search_candidate_kind": "derived_clip"}
            if candidate_kind == "derived_clip"
            else {}
        ),
    )
    item = result.get("item") if isinstance(result.get("item"), dict) else {}
    return {
        **item,
        "skipped": bool(result.get("reused")),
        "local_reuse": False,
        "global_media_library": True,
        "source_kind": (
            "media_library_clip"
            if candidate_kind == "derived_clip"
            else "media_library_original"
        ),
        "source_asset_id": source_asset_id,
        "source_clip_id": (
            source_clip_id if candidate_kind == "derived_clip" else None
        ),
        "source_version": _text(result.get("source_version")),
        "import_id": _text(result.get("import_id")),
        "media_library_search_id": shared_search_id,
    }


def _asset_manifest_item(candidate: dict[str, Any], rel_path: str, filename: str, label: str, content_sha256: str, search_id: str, created_at: int) -> dict[str, Any]:
    _root, asset_type, kind, _allowed = _asset_kind_payload(_text(candidate.get("media_type"), "image"))
    return {
        "id": rel_path,
        "path": rel_path,
        "label": label or _text(candidate.get("title"), filename),
        "filename": filename,
        "asset_type": asset_type,
        "kind": kind,
        "source": "asset_search",
        "created_at": created_at,
        "content_sha256": content_sha256,
        "origin": {
            "tool": "asset_search_agent",
            "search_id": search_id,
            "candidate_id": _text(candidate.get("candidate_id")),
            "provider": _text(candidate.get("provider")),
            "media_type": kind,
            "provider_asset_id": _text(candidate.get("provider_asset_id")),
            "source_url": _text(candidate.get("source_url")),
            "download_url": _text(candidate.get("download_url")),
            "creator": candidate.get("creator") if isinstance(candidate.get("creator"), dict) else {},
            "license": candidate.get("license") if isinstance(candidate.get("license"), dict) else {},
            "query": _text(candidate.get("query")),
            "score": candidate.get("score"),
            "storyboard_ref": candidate.get("storyboard_ref") if isinstance(candidate.get("storyboard_ref"), dict) else {},
        },
    }


def asset_search_source_list(task: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for asset in _manifest_assets(sc.workspace_for(task), sc=sc):
        origin = asset.get("origin") if isinstance(asset.get("origin"), dict) else {}
        if origin.get("tool") != "asset_search_agent":
            continue
        license_info = origin.get("license") if isinstance(origin.get("license"), dict) else {}
        creator = origin.get("creator") if isinstance(origin.get("creator"), dict) else {}
        items.append({
            "asset_path": _text(asset.get("path")),
            "label": _text(asset.get("label") or asset.get("filename")),
            "media_type": _text(origin.get("media_type") or asset.get("kind")),
            "provider": _text(origin.get("provider")),
            "provider_asset_id": _text(origin.get("provider_asset_id")),
            "source_url": _text(origin.get("source_url")),
            "download_url": _text(origin.get("download_url")),
            "creator": {"name": _text(creator.get("name")), "url": _text(creator.get("url"))},
            "license": {
                "name": _text(license_info.get("name")),
                "url": _text(license_info.get("url")),
                "requires_attribution": bool(license_info.get("requires_attribution")),
                "attribution_text": _text(license_info.get("attribution_text")),
                "license_status": _text(license_info.get("license_status")),
            },
            "search_id": _text(origin.get("search_id")),
            "candidate_id": _text(origin.get("candidate_id")),
            "query": _text(origin.get("query")),
            "storyboard_ref": origin.get("storyboard_ref") if isinstance(origin.get("storyboard_ref"), dict) else {},
        })
    items.sort(key=lambda item: (item["media_type"], item["provider"], item["asset_path"]))
    return {
        "schema_version": ASSET_SEARCH_SOURCE_LIST_SCHEMA,
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "created_at": now_ms(),
        "items": items,
    }


def _asset_search_source_list_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Asset Search Source List",
        "",
        f"- Task ID: {payload.get('task_id')}",
        f"- Session ID: {payload.get('session_id')}",
        f"- Item count: {len(payload.get('items') or [])}",
        "",
        "| Asset | Type | Provider | Creator | License | Source |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in payload.get("items") or []:
        license_info = item.get("license") if isinstance(item.get("license"), dict) else {}
        creator = item.get("creator") if isinstance(item.get("creator"), dict) else {}
        source_url = _text(item.get("source_url"))
        source = f"[source]({source_url})" if source_url else ""
        lines.append(
            "| "
            + " | ".join([
                _text(item.get("asset_path")).replace("|", "\\|"),
                _text(item.get("media_type")).replace("|", "\\|"),
                _text(item.get("provider")).replace("|", "\\|"),
                _text(creator.get("name"), "Unknown").replace("|", "\\|"),
                _text(license_info.get("name"), "Unknown").replace("|", "\\|"),
                source,
            ])
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def export_asset_search_source_list(task: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    payload = asset_search_source_list(task, sc=sc)
    sc.write_json(workspace / ASSET_SEARCH_SOURCE_LIST_JSON_REL, payload)
    markdown = _asset_search_source_list_markdown(payload)
    markdown_path = workspace / ASSET_SEARCH_SOURCE_LIST_MD_REL
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.source_list.exported", {
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "item_count": len(payload.get("items") or []),
        "json_path": ASSET_SEARCH_SOURCE_LIST_JSON_REL,
        "markdown_path": ASSET_SEARCH_SOURCE_LIST_MD_REL,
    })
    return {
        "ok": True,
        "item_count": len(payload.get("items") or []),
        "json_path": ASSET_SEARCH_SOURCE_LIST_JSON_REL,
        "markdown_path": ASSET_SEARCH_SOURCE_LIST_MD_REL,
        "items": payload.get("items") or [],
    }


async def import_asset_search_candidates(task: dict[str, Any], payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    search_id = _text((payload or {}).get("search_id"))
    candidate_ids = [_text(item) for item in (payload or {}).get("candidate_ids") or [] if _text(item)]
    label_prefix = _text((payload or {}).get("label_prefix"))
    dialogue_asset_key = _text((payload or {}).get("dialogue_asset_key"))
    confirm_license = _bool((payload or {}).get("confirm_license"), False)
    if not search_id or not candidate_ids:
        raise HTTPException(status_code=400, detail="search_id and candidate_ids are required")
    if len(candidate_ids) > ASSET_SEARCH_MAX_IMPORT_COUNT:
        raise HTTPException(status_code=400, detail={"message": "Asset search imports support at most 12 candidates", "limit": ASSET_SEARCH_MAX_IMPORT_COUNT})
    run = load_asset_search_run(task, search_id, sc=sc)
    imported: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.import.requested", {
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "search_id": search_id,
        "candidate_count": len(candidate_ids),
    })
    for index, candidate_id in enumerate(candidate_ids, start=1):
        candidate = _candidate_by_id(run, candidate_id)
        if not candidate:
            failed.append({"candidate_id": candidate_id, "reason": "candidate_not_found"})
            continue
        if _text(candidate.get("provider")).lower() == "local":
            existing_local = asset_search_manifest_existing_local_candidate(workspace, candidate, sc=sc)
            if existing_local:
                imported.append({**existing_local, "skipped": True, "local_reuse": True})
            else:
                failed.append({"candidate_id": candidate_id, "reason": "local_asset_not_found"})
            continue
        if _text(candidate.get("provider")).lower() == "media_library":
            try:
                imported.append(
                    await _import_media_library_candidate(
                        task,
                        agent_search_id=search_id,
                        candidate=candidate,
                        label_prefix=label_prefix,
                        dialogue_asset_key=dialogue_asset_key,
                        sc=sc,
                    )
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                failed.append(
                    {
                        "candidate_id": candidate_id,
                        "reason": _text(
                            detail.get("code"),
                            "media_library_import_failed",
                        ),
                        "detail": _safe_error_detail(
                            detail.get("user_message")
                            or detail.get("message")
                            or exc,
                            sc=sc,
                        ),
                    }
                )
            except Exception as exc:
                failed.append(
                    {
                        "candidate_id": candidate_id,
                        "reason": _text(
                            exc, "media_library_import_failed"
                        ),
                    }
                )
            continue
        if candidate.get("import_supported") is False:
            failed.append({"candidate_id": candidate_id, "reason": "import_not_supported", "detail": _text(candidate.get("import_unsupported_reason"))})
            continue
        license_error = _license_import_error(candidate, confirm_license)
        if license_error:
            failed.append({"candidate_id": candidate_id, "reason": license_error})
            continue
        existing = asset_search_manifest_existing_candidate(workspace, candidate, sc=sc)
        if existing:
            imported.append({**existing, "skipped": True})
            continue
        refreshed, refresh_error = await _refresh_stale_candidate(candidate, run, sc=sc)
        if refresh_error or not refreshed:
            failed.append({"candidate_id": candidate_id, "reason": refresh_error or "stale_candidate_url"})
            continue
        candidate = refreshed
        if candidate.get("import_supported") is False:
            failed.append({"candidate_id": candidate_id, "reason": "import_not_supported", "detail": _text(candidate.get("import_unsupported_reason"))})
            continue
        license_error = _license_import_error(candidate, confirm_license)
        if license_error:
            failed.append({"candidate_id": candidate_id, "reason": license_error})
            continue
        provider_id = _text(candidate.get("provider")).lower()
        media_type = _text(candidate.get("media_type"), "image").lower()
        if media_type not in ASSET_SEARCH_MEDIA_TYPES:
            failed.append({"candidate_id": candidate_id, "reason": "import_not_supported", "detail": "invalid_media_type"})
            continue
        try:
            provider = provider_for(provider_id, _provider_credentials(sc=sc).get(provider_id, ""))
            prepare_import_candidate = getattr(provider, "prepare_import_candidate", None)
            if callable(prepare_import_candidate):
                candidate = await prepare_import_candidate(candidate)
            if candidate.get("import_supported") is False:
                failed.append({"candidate_id": candidate_id, "reason": "import_not_supported", "detail": _text(candidate.get("import_unsupported_reason"))})
                continue
            license_error = _license_import_error(candidate, confirm_license)
            if license_error:
                failed.append({"candidate_id": candidate_id, "reason": license_error})
                continue
            media_type = _text(candidate.get("media_type"), media_type).lower()
            if media_type not in ASSET_SEARCH_MEDIA_TYPES:
                failed.append({"candidate_id": candidate_id, "reason": "import_not_supported", "detail": "invalid_media_type"})
                continue
            validate_provider_url(provider_id, _text(candidate.get("download_url")))
            max_bytes = ASSET_SEARCH_FILE_SIZE_LIMITS.get(media_type, ASSET_SEARCH_FILE_SIZE_LIMITS["image"])
            rel_path = asset_search_target_rel_path(candidate, index, label_prefix)
            rel_path, target_path = _unique_target_path(workspace, rel_path, media_type, sc=sc)
            temp_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.part")
            download_info = await asset_search_providers.download_candidate_file(
                provider_id,
                _text(candidate.get("download_url")),
                temp_path,
                max_bytes=max_bytes,
                timeout=120 if media_type == "video" else 60,
            )
            _validate_downloaded_file(temp_path, media_type, _text(candidate.get("mime_type")), _text(download_info.get("content_type")), max_bytes, target_path.suffix)
            existing_hash = _manifest_existing_hash(workspace, _text(download_info.get("content_sha256")), sc=sc)
            if existing_hash:
                temp_path.unlink(missing_ok=True)
                imported.append({**existing_hash, "skipped": True})
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.replace(target_path)
            filename = target_path.name
            label = label_prefix or _text(candidate.get("title"), filename)
            asset = _asset_manifest_item(candidate, rel_path, filename, label, _text(download_info.get("content_sha256")), search_id, now_ms())
            sidecar_path = target_path.with_suffix(".json")
            sc.write_json(sidecar_path, {
                "schema_version": ASSET_SEARCH_IMPORT_SCHEMA,
                "candidate": candidate,
                "download": download_info,
                "asset": asset,
            })
            sc.upsert_asset_manifest_item(workspace, asset, sc=sc)
            imported.append({**asset, "skipped": False})
        except AssetSearchSecurityError as exc:
            failed.append({"candidate_id": candidate_id, "reason": "ssrf_host_not_allowed", "detail": _safe_error_detail(exc, sc=sc)})
        except Exception as exc:
            failed.append({"candidate_id": candidate_id, "reason": "download_failed", "detail": _safe_error_detail(exc, sc=sc)})
            try:
                temp_path.unlink(missing_ok=True)  # type: ignore[name-defined]
            except Exception:
                pass
    import_record = {
        "schema_version": ASSET_SEARCH_IMPORT_SCHEMA,
        "import_id": f"import_{now_ms()}_{uuid.uuid4().hex[:6]}",
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "search_id": search_id,
        "created_at": now_ms(),
        "imported": imported,
        "failed": failed,
    }
    sc.write_json(workspace / ASSET_SEARCH_IMPORTS_REL / f"{import_record['import_id']}.json", import_record)
    if failed:
        reasons: dict[str, int] = {}
        for item in failed:
            reason = _text(item.get("reason"), "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1
        sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.import.failed", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "search_id": search_id,
            "failed_count": len(failed),
            "reasons": reasons,
        })
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_search.import.completed", {
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "search_id": search_id,
        "imported_count": len(imported),
        "failed_count": len(failed),
        "skipped_count": len([item for item in imported if item.get("skipped")]),
    })
    try:
        plan, meta = sc.load_plan(task, sc=sc)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        empty_payload = sc.empty_asset_library_payload(
            task,
            workspace,
            sc=sc,
        )
        plan = empty_payload["plan"]
        meta = empty_payload["meta"]
    return {
        "ok": True,
        "task": task,
        "meta": meta,
        "plan": plan,
        "imported": imported,
        "failed": failed,
        "added": imported,
    }
