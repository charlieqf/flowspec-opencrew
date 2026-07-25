from __future__ import annotations

import copy
import json
import os
from typing import Any

from fastapi import HTTPException, Request

from .context import AppContext
from .routes.auth import AUTH_ROLE_ADMIN, AUTH_ROLE_USER


SURFACE_OPENFLOW_PROMPT = "openflow.prompt"
SURFACE_ANALYSIS_V1_PROMPT = "analysis_v1.prompt"
SURFACE_ANALYSIS_V1_RUN = "analysis_v1.run"
SURFACE_KOUBO_TTS_TIMING = "koubo_storyboard.tts_timing"
SURFACE_KOUBO_HOST_PRODUCT_PROMPT = "koubo.host_product.prompt"
SURFACE_KOUBO_ASSET_AGENT_CHAT = "koubo.asset_library.agent_chat"
SURFACE_KOUBO_STORYBOARD_EDIT_AGENT_CHAT = "koubo.storyboard.edit_agent_chat"
SURFACE_KOUBO_IMAGE_PLAN_AGENT_CHAT = "koubo.image_plan.agent_chat"
SURFACE_KOUBO_VIDEO_PLAN_AGENT_CHAT = "koubo.video_plan.agent_chat"
SURFACE_KOUBO_COMPOSER_AGENT_CHAT = "koubo.composer.agent_chat"
SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC = "media_library.visual_semantic"
SURFACE_MEDIA_LIBRARY_COMPOSITE = "media_library.composite"
SURFACE_MEDIA_LIBRARY_SEARCH_PLANNER = "media_library.search_planner"

DEFAULT_USER_MODEL_POLICY: dict[str, Any] = {
    "surfaces": {
        SURFACE_OPENFLOW_PROMPT: {
            "mode": "alias",
            "options": [
                {
                    "provider_alias": "Max",
                    "provider_label": "Max",
                    "model_alias": "Max",
                    "model_label": "Max",
                    "provider": "openai",
                    "provider_label_real": "OpenAI",
                    "model": "gpt-5.5",
                    "model_label_real": "GPT-5.5",
                },
                {
                    "provider_alias": "Flash",
                    "provider_label": "Flash",
                    "model_alias": "Flash",
                    "model_label": "Flash",
                    "provider": "opencode",
                    "provider_label_real": "OpenCode Zen",
                    "model": "deepseek-v4-flash-free",
                    "model_label_real": "DeepSeek v4 flash free",
                },
            ],
        },
        SURFACE_ANALYSIS_V1_PROMPT: {
            "mode": "alias",
            "options": [
                {
                    "provider_alias": "Max",
                    "provider_label": "Max",
                    "model_alias": "Max",
                    "model_label": "Max",
                    "provider": "openai",
                    "provider_label_real": "OpenAI",
                    "model": "gpt-5.5",
                    "model_label_real": "GPT-5.5",
                },
                {
                    "provider_alias": "Flash",
                    "provider_label": "Flash",
                    "model_alias": "Flash",
                    "model_label": "Flash",
                    "provider": "opencode",
                    "provider_label_real": "OpenCode Zen",
                    "model": "deepseek-v4-flash-free",
                    "model_label_real": "DeepSeek v4 flash free",
                },
            ],
        },
        SURFACE_ANALYSIS_V1_RUN: {
            "mode": "alias",
            "fixed_fields": {
                "asr_mode": "default",
                "allow_cloud_asr_data_transfer": True,
            },
            "options": [
                {
                    "provider_alias": "Max",
                    "provider_label": "Max",
                    "model_alias": "Max",
                    "model_label": "Max",
                    "provider": "openai",
                    "provider_label_real": "OpenAI",
                    "model": "gpt-5.5",
                    "model_label_real": "GPT-5.5",
                },
                {
                    "provider_alias": "Flash",
                    "provider_label": "Flash",
                    "model_alias": "Flash",
                    "model_label": "Flash",
                    "provider": "opencode",
                    "provider_label_real": "OpenCode Zen",
                    "model": "deepseek-v4-flash-free",
                    "model_label_real": "DeepSeek v4 flash free",
                },
            ],
        },
        SURFACE_KOUBO_TTS_TIMING: {
            "mode": "hide",
            "defaults": {
                "provider": "google",
                "model": "gemini-3.1-flash-tts-preview",
            },
            "visible_fields": ["voice", "tts_tempo"],
        },
        SURFACE_KOUBO_HOST_PRODUCT_PROMPT: {
            "mode": "alias",
            "options": [
                {
                    "provider_alias": "Max",
                    "provider_label": "Max",
                    "model_alias": "Max",
                    "model_label": "Max",
                    "provider": "openai",
                    "provider_label_real": "OpenAI",
                    "model": "gpt-5.5",
                    "model_label_real": "GPT-5.5",
                },
                {
                    "provider_alias": "Flash",
                    "provider_label": "Flash",
                    "model_alias": "Flash",
                    "model_label": "Flash",
                    "provider": "opencode",
                    "provider_label_real": "OpenCode Zen",
                    "model": "deepseek-v4-flash-free",
                    "model_label_real": "DeepSeek v4 flash free",
                }
            ],
        },
        SURFACE_KOUBO_ASSET_AGENT_CHAT: {
            "mode": "alias",
            "options": [
                {
                    "provider_alias": "Max",
                    "provider_label": "Max",
                    "model_alias": "Max",
                    "model_label": "Max",
                    "provider": "openai",
                    "provider_label_real": "OpenAI",
                    "model": "gpt-5.5",
                    "model_label_real": "GPT-5.5",
                },
                {
                    "provider_alias": "Flash",
                    "provider_label": "Flash",
                    "model_alias": "Flash",
                    "model_label": "Flash",
                    "provider": "opencode",
                    "provider_label_real": "OpenCode Zen",
                    "model": "deepseek-v4-flash-free",
                    "model_label_real": "DeepSeek v4 flash free",
                }
            ],
        },
    }
}

for _surface in (
    SURFACE_KOUBO_STORYBOARD_EDIT_AGENT_CHAT,
    SURFACE_KOUBO_IMAGE_PLAN_AGENT_CHAT,
    SURFACE_KOUBO_VIDEO_PLAN_AGENT_CHAT,
    SURFACE_KOUBO_COMPOSER_AGENT_CHAT,
):
    DEFAULT_USER_MODEL_POLICY["surfaces"][_surface] = copy.deepcopy(
        DEFAULT_USER_MODEL_POLICY["surfaces"][SURFACE_KOUBO_ASSET_AGENT_CHAT]
    )

for _surface, _version, _required_input_modalities in (
    (
        SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC,
        "visual_semantic_default_v1",
        ["image"],
    ),
    (
        SURFACE_MEDIA_LIBRARY_COMPOSITE,
        "composite_default_v1",
        [],
    ),
    (
        SURFACE_MEDIA_LIBRARY_SEARCH_PLANNER,
        "search_planner_default_v1",
        [],
    ),
):
    _config = copy.deepcopy(
        DEFAULT_USER_MODEL_POLICY["surfaces"][
            SURFACE_KOUBO_ASSET_AGENT_CHAT
        ]
    )
    _config.update(
        {
            "alias_only": True,
            "read_only": True,
            "version": _version,
            "required_input_modalities": _required_input_modalities,
        }
    )
    DEFAULT_USER_MODEL_POLICY["surfaces"][_surface] = _config


MODEL_FIELD_PAIRS = (
    ("prompt_model_provider", "prompt_model_id"),
    ("run_model_provider", "run_model_id"),
    ("skill_model_provider", "skill_model_id"),
    ("used_prompt_model_provider", "used_prompt_model_id"),
    ("used_run_model_provider", "used_run_model_id"),
    ("providerID", "modelID"),
    ("provider", "model"),
    ("provider", "model_id"),
    ("model_provider", "model_id"),
)


def request_role(request: Request | None) -> str:
    role = str(getattr(getattr(request, "state", None), "opencrew_auth_role", "") or "").strip()
    return role if role in {AUTH_ROLE_ADMIN, AUTH_ROLE_USER} else AUTH_ROLE_USER


def user_model_policy(ctx: AppContext) -> dict[str, Any]:
    path = os.environ.get("OPENCREW_USER_MODEL_POLICY_PATH", "").strip()
    if not path:
        return copy.deepcopy(DEFAULT_USER_MODEL_POLICY)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed loading user model policy: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=500, detail="User model policy must be a JSON object")
    return parsed


def surface_policy(ctx: AppContext, surface: str) -> dict[str, Any]:
    policy = user_model_policy(ctx)
    surfaces = policy.get("surfaces") if isinstance(policy.get("surfaces"), dict) else {}
    value = surfaces.get(surface) or {}
    return value if isinstance(value, dict) else {}


def policy_mode(ctx: AppContext, surface: str) -> str:
    return str(surface_policy(ctx, surface).get("mode") or "").strip().lower()


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _same(value: Any, candidate: Any) -> bool:
    return bool(_norm(value)) and _norm(value) == _norm(candidate)


def _option_real_provider_values(option: dict[str, Any]) -> set[str]:
    return {
        _norm(option.get("provider")),
        _norm(option.get("provider_label_real")),
        _norm(option.get("provider_name")),
        _norm(option.get("providerName")),
    } - {""}


def _option_real_model_values(option: dict[str, Any]) -> set[str]:
    return {
        _norm(option.get("model")),
        _norm(option.get("model_label_real")),
        _norm(option.get("model_name")),
        _norm(option.get("modelName")),
    } - {""}


def _option_alias_provider_values(option: dict[str, Any]) -> set[str]:
    return {
        _norm(option.get("provider_alias")),
        _norm(option.get("provider_label")),
    } - {""}


def _option_alias_model_values(option: dict[str, Any]) -> set[str]:
    return {
        _norm(option.get("model_alias")),
        _norm(option.get("model_label")),
    } - {""}


def _alias_options(ctx: AppContext, surface: str) -> list[dict[str, Any]]:
    policy = surface_policy(ctx, surface)
    options = policy.get("options") if isinstance(policy.get("options"), list) else []
    return [item for item in options if isinstance(item, dict)]


def _all_alias_options(ctx: AppContext) -> list[dict[str, Any]]:
    policy = user_model_policy(ctx)
    surfaces = policy.get("surfaces") if isinstance(policy.get("surfaces"), dict) else {}
    items: list[dict[str, Any]] = []
    for config in surfaces.values():
        if not isinstance(config, dict) or str(config.get("mode") or "").strip().lower() != "alias":
            continue
        options = config.get("options") if isinstance(config.get("options"), list) else []
        items.extend([item for item in options if isinstance(item, dict)])
    return items


def _item_matches_option(item: dict[str, Any], option: dict[str, Any]) -> bool:
    provider_values = {_norm(item.get("providerID")), _norm(item.get("providerName"))} - {""}
    model_values = {_norm(item.get("modelID")), _norm(item.get("modelName"))} - {""}
    return bool(provider_values & _option_real_provider_values(option)) and bool(model_values & _option_real_model_values(option))


def _catalog_item_for_option(prompt_models: dict[str, Any], option: dict[str, Any]) -> dict[str, Any] | None:
    for item in prompt_models.get("items") or []:
        if isinstance(item, dict) and _item_matches_option(item, option):
            return item
    return None


def _option_for_alias(ctx: AppContext, surface: str, provider: str, model_id: str) -> dict[str, Any] | None:
    for option in _alias_options(ctx, surface):
        if _norm(provider) in _option_alias_provider_values(option) and _norm(model_id) in _option_alias_model_values(option):
            return option
    return None


def _option_for_allowed_selection(ctx: AppContext, surface: str, provider: str, model_id: str) -> dict[str, Any] | None:
    option = _option_for_alias(ctx, surface, provider, model_id)
    if option is not None:
        return option
    if bool(surface_policy(ctx, surface).get("alias_only")):
        return None
    return _option_for_real(ctx, provider, model_id, surface=surface)


def _option_for_real(ctx: AppContext, provider: str, model_id: str, *, surface: str | None = None) -> dict[str, Any] | None:
    options = _alias_options(ctx, surface) if surface else _all_alias_options(ctx)
    for option in options:
        if _norm(provider) in _option_real_provider_values(option) and _norm(model_id) in _option_real_model_values(option):
            return option
    return None


def _masked_item(option: dict[str, Any], real_item: dict[str, Any] | None = None) -> dict[str, Any]:
    source = dict(real_item or {})
    source.update(
        {
            "providerID": str(option.get("provider_alias") or option.get("provider_label") or ""),
            "providerName": str(option.get("provider_label") or option.get("provider_alias") or ""),
            "modelID": str(option.get("model_alias") or option.get("model_label") or ""),
            "modelName": str(option.get("model_label") or option.get("model_alias") or ""),
            "source": "alias",
        }
    )
    return source


def _raw_model_available(prompt_models: dict[str, Any], provider: str, model_id: str) -> bool:
    for item in prompt_models.get("items") or []:
        if not isinstance(item, dict):
            continue
        if {_norm(item.get("providerID")), _norm(item.get("providerName"))} & {_norm(provider)} and {_norm(item.get("modelID")), _norm(item.get("modelName"))} & {_norm(model_id)}:
            return True
    return False


def mask_prompt_models_for_role(ctx: AppContext, role: str, surface: str, prompt_models: dict[str, Any]) -> dict[str, Any]:
    if role == AUTH_ROLE_ADMIN:
        return prompt_models
    mode = policy_mode(ctx, surface)
    if mode == "hide":
        payload: dict[str, Any] = {"items": [], "default_model": {"providerID": "", "modelID": ""}}
        if prompt_models.get("error"):
            payload["error"] = prompt_models.get("error")
        return payload
    if mode != "alias":
        return prompt_models
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    required_modalities = surface_policy(ctx, surface).get(
        "required_input_modalities"
    )
    for option in _alias_options(ctx, surface):
        real_item = _catalog_item_for_option(prompt_models, option)
        if not real_item:
            continue
        resolved = {
            "providerID": str(option.get("provider") or ""),
            "modelID": str(option.get("model") or ""),
        }
        if isinstance(required_modalities, list) and any(
            not model_supports_input_modality(
                prompt_models, resolved, str(modality)
            )
            for modality in required_modalities
            if str(modality).strip()
        ):
            continue
        masked = _masked_item(option, real_item)
        key = (str(masked["providerID"]), str(masked["modelID"]))
        if key in seen:
            continue
        seen.add(key)
        items.append(masked)
    default_model = {"providerID": "", "modelID": ""}
    if items:
        default_model = {"providerID": str(items[0]["providerID"]), "modelID": str(items[0]["modelID"])}
    payload = {"items": items, "default_model": default_model}
    if prompt_models.get("error"):
        payload["error"] = (
            "Approved model catalog is unavailable"
            if bool(surface_policy(ctx, surface).get("alias_only"))
            else prompt_models.get("error")
        )
    return payload


def model_surface_policy_for_role(
    ctx: AppContext, role: str, surface: str
) -> dict[str, Any]:
    """Return a public surface descriptor without exposing alias targets."""

    policy = surface_policy(ctx, surface)
    if role == AUTH_ROLE_ADMIN:
        return copy.deepcopy(policy)
    mode = str(policy.get("mode") or "").strip().lower()
    payload: dict[str, Any] = {
        "surface": surface,
        "mode": mode,
        "version": str(policy.get("version") or ""),
        "read_only": bool(policy.get("read_only", True)),
    }
    required = policy.get("required_input_modalities")
    if isinstance(required, list):
        payload["required_input_modalities"] = [
            str(item).strip().lower()
            for item in required
            if str(item).strip()
        ]
    if mode == "alias":
        payload["options"] = [
            {
                "provider_alias": str(
                    option.get("provider_alias")
                    or option.get("provider_label")
                    or ""
                ),
                "provider_label": str(
                    option.get("provider_label")
                    or option.get("provider_alias")
                    or ""
                ),
                "model_alias": str(
                    option.get("model_alias")
                    or option.get("model_label")
                    or ""
                ),
                "model_label": str(
                    option.get("model_label")
                    or option.get("model_alias")
                    or ""
                ),
            }
            for option in _alias_options(ctx, surface)
        ]
    return payload


def _catalog_item_for_model(
    prompt_models: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any] | None:
    provider = _norm(model.get("providerID") or model.get("provider"))
    model_id = _norm(model.get("modelID") or model.get("model"))
    if not provider or not model_id:
        return None
    for item in prompt_models.get("items") or []:
        if not isinstance(item, dict):
            continue
        providers = {
            _norm(item.get("providerID")),
            _norm(item.get("providerName")),
            _norm(item.get("provider")),
        } - {""}
        model_ids = {
            _norm(item.get("modelID")),
            _norm(item.get("modelName")),
            _norm(item.get("model")),
        } - {""}
        if provider in providers and model_id in model_ids:
            return item
    return None


def _normalized_capability(value: Any) -> str:
    return " ".join(
        part
        for part in str(value or "")
        .strip()
        .lower()
        .replace("_", " ")
        .replace("-", " ")
        .split()
        if part
    )


def model_supports_input_modality(
    prompt_models: dict[str, Any],
    model: dict[str, Any],
    modality: str,
) -> bool:
    """Strictly verify an input modality from the connected model catalog."""

    item = _catalog_item_for_model(prompt_models, model)
    if item is None:
        return False
    values: list[Any] = []
    input_modalities = item.get("inputModalities")
    if isinstance(input_modalities, list):
        values.extend(input_modalities)
    modalities = item.get("modalities")
    if isinstance(modalities, dict) and isinstance(
        modalities.get("input"), list
    ):
        values.extend(modalities["input"])
    capabilities = item.get("capabilities")
    if isinstance(capabilities, list):
        values.extend(capabilities)
    elif isinstance(capabilities, dict):
        values.extend(
            key
            for key, enabled in capabilities.items()
            if enabled is True
        )
        capability_input = capabilities.get("input")
        if isinstance(capability_input, list):
            values.extend(capability_input)
        elif isinstance(capability_input, dict):
            values.extend(
                key
                for key, enabled in capability_input.items()
                if enabled is True
            )
    expected = _normalized_capability(modality)
    for value in values:
        normalized = _normalized_capability(value)
        if normalized == expected or normalized == f"{expected} input":
            return True
    return False


def require_model_input_capability(
    prompt_models: dict[str, Any],
    model: dict[str, str],
    modality: str,
) -> dict[str, str]:
    if model_supports_input_modality(prompt_models, model, modality):
        return model
    expected = _normalized_capability(modality) or "required"
    raise HTTPException(
        status_code=409,
        detail={
            "code": "model_input_capability_missing",
            "user_message": (
                f"当前模型别名不支持所需的 {expected} input，"
                "请联系管理员配置已批准的兼容模型。"
            ),
            "required_input_modality": expected,
        },
    )


def require_image_input_capability(
    prompt_models: dict[str, Any], model: dict[str, str]
) -> dict[str, str]:
    return require_model_input_capability(prompt_models, model, "image")


def require_surface_model_capabilities(
    ctx: AppContext,
    surface: str,
    prompt_models: dict[str, Any],
    model: dict[str, str],
) -> dict[str, str]:
    required = surface_policy(ctx, surface).get(
        "required_input_modalities"
    )
    if not isinstance(required, list):
        return model
    for modality in required:
        if str(modality).strip():
            require_model_input_capability(
                prompt_models, model, str(modality)
            )
    return model


def _resolve_admin_model(prompt_models: dict[str, Any], provider: str, model_id: str, purpose: str) -> dict[str, str]:
    provider = str(provider or "").strip()
    model_id = str(model_id or "").strip()
    if provider and model_id:
        if not _raw_model_available(prompt_models, provider, model_id):
            raise HTTPException(status_code=400, detail=f"{purpose} model not found: {provider}/{model_id}")
        return {"providerID": provider, "modelID": model_id}
    default_model = prompt_models.get("default_model") or {}
    provider = str(default_model.get("providerID") or "").strip()
    model_id = str(default_model.get("modelID") or "").strip()
    if provider and model_id:
        return {"providerID": provider, "modelID": model_id}
    raise HTTPException(status_code=400, detail=f"No {purpose.lower()} models are available")


def resolve_prompt_model_for_role(
    ctx: AppContext,
    role: str,
    surface: str,
    prompt_models: dict[str, Any],
    provider: str,
    model_id: str,
    purpose: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    if role == AUTH_ROLE_ADMIN:
        resolved = _resolve_admin_model(
            prompt_models, provider, model_id, purpose
        )
        return (
            require_surface_model_capabilities(
                ctx, surface, prompt_models, resolved
            ),
            prompt_models,
        )
    mode = policy_mode(ctx, surface)
    if mode == "hide":
        if str(provider or "").strip() or str(model_id or "").strip():
            raise HTTPException(status_code=403, detail=f"{purpose} model selection is not available for this role")
        defaults = surface_policy(ctx, surface).get("defaults") or {}
        real_provider = str(defaults.get("provider") or "").strip()
        real_model = str(defaults.get("model") or "").strip()
        if not real_provider or not real_model:
            raise HTTPException(status_code=500, detail=f"{purpose} model policy is missing defaults")
        resolved = {"providerID": real_provider, "modelID": real_model}
        return (
            require_surface_model_capabilities(
                ctx, surface, prompt_models, resolved
            ),
            mask_prompt_models_for_role(
                ctx, role, surface, prompt_models
            ),
        )
    if mode != "alias":
        resolved = _resolve_admin_model(
            prompt_models, provider, model_id, purpose
        )
        return (
            require_surface_model_capabilities(
                ctx, surface, prompt_models, resolved
            ),
            prompt_models,
        )

    requested_provider = str(provider or "").strip()
    requested_model = str(model_id or "").strip()
    option = _option_for_allowed_selection(ctx, surface, requested_provider, requested_model) if requested_provider and requested_model else None
    if not option and requested_provider and requested_model:
        raise HTTPException(status_code=403, detail=f"{purpose} model is not available for this role")
    if option is None:
        for candidate in _alias_options(ctx, surface):
            if not _catalog_item_for_option(prompt_models, candidate):
                continue
            candidate_model = {
                "providerID": str(candidate.get("provider") or ""),
                "modelID": str(candidate.get("model") or ""),
            }
            required = surface_policy(ctx, surface).get(
                "required_input_modalities"
            )
            if isinstance(required, list) and any(
                not model_supports_input_modality(
                    prompt_models, candidate_model, str(modality)
                )
                for modality in required
                if str(modality).strip()
            ):
                continue
            option = candidate
            break
    if option is None:
        message = (
            f"No approved {purpose.lower()} models are available"
            if bool(surface_policy(ctx, surface).get("alias_only"))
            else f"No OpenCode {purpose.lower()} models are available"
        )
        raise HTTPException(status_code=400, detail=message)
    if not _catalog_item_for_option(prompt_models, option):
        raise HTTPException(status_code=400, detail=f"{purpose} model is not connected")
    resolved = {
        "providerID": str(option.get("provider") or ""),
        "modelID": str(option.get("model") or ""),
    }
    return (
        require_surface_model_capabilities(
            ctx, surface, prompt_models, resolved
        ),
        mask_prompt_models_for_role(ctx, role, surface, prompt_models),
    )


def fixed_fields_update_for_role(
    ctx: AppContext,
    role: str,
    surface: str,
    values: dict[str, Any],
    provided_fields: set[str] | None = None,
) -> dict[str, Any]:
    if role == AUTH_ROLE_ADMIN:
        return {}
    fixed = surface_policy(ctx, surface).get("fixed_fields") or {}
    if not isinstance(fixed, dict):
        return {}
    # Fixed fields are server-owned for non-admin users. Submitted values are ignored
    # and overwritten so stale clients cannot block the workflow with policy errors.
    return dict(fixed)


def hidden_model_defaults_for_role(
    ctx: AppContext,
    role: str,
    surface: str,
    provider: str = "",
    model_id: str = "",
) -> dict[str, str]:
    if role == AUTH_ROLE_ADMIN:
        return {"provider": provider, "model": model_id}
    policy = surface_policy(ctx, surface)
    if str(policy.get("mode") or "").strip().lower() != "hide":
        return {"provider": provider, "model": model_id}
    if str(provider or "").strip() or str(model_id or "").strip():
        raise HTTPException(status_code=403, detail="Model selection is not available for this role")
    defaults = policy.get("defaults") or {}
    return {"provider": str(defaults.get("provider") or ""), "model": str(defaults.get("model") or "")}


def _mask_pair(ctx: AppContext, surface: str, provider: Any, model_id: Any) -> tuple[str, str] | None:
    option = _option_for_real(ctx, str(provider or ""), str(model_id or ""), surface=surface)
    if not option:
        return None
    return (
        str(option.get("provider_alias") or option.get("provider_label") or ""),
        str(option.get("model_alias") or option.get("model_label") or ""),
    )


def _alias_pair(ctx: AppContext, surface: str, provider: Any, model_id: Any) -> tuple[str, str] | None:
    for option in _alias_options(ctx, surface):
        if _norm(provider) in _option_alias_provider_values(option) and _norm(model_id) in _option_alias_model_values(option):
            return (
                str(option.get("provider_alias") or option.get("provider_label") or ""),
                str(option.get("model_alias") or option.get("model_label") or ""),
            )
    return None


def _apply_pair(result: dict[str, Any], provider_key: str, model_key: str, provider: str, model_id: str) -> None:
    result[provider_key], result[model_key] = provider, model_id
    if provider_key == "providerID":
        result["providerName"] = provider
    if model_key == "modelID":
        result["modelName"] = model_id


def _mask_payload_value(ctx: AppContext, surface: str, value: Any) -> Any:
    if isinstance(value, list):
        return [_mask_payload_value(ctx, surface, item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _mask_payload_value(ctx, surface, item) for key, item in value.items()}
    for provider_key, model_key in MODEL_FIELD_PAIRS:
        if provider_key not in result or model_key not in result:
            continue
        if not (_norm(result.get(provider_key)) or _norm(result.get(model_key))):
            continue
        masked = _mask_pair(ctx, surface, result.get(provider_key), result.get(model_key))
        if not masked:
            masked = _alias_pair(ctx, surface, result.get(provider_key), result.get(model_key))
        if masked:
            _apply_pair(result, provider_key, model_key, masked[0], masked[1])
        else:
            _apply_pair(result, provider_key, model_key, "", "")
    return result


def _hide_payload_model_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_hide_payload_model_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _hide_payload_model_fields(item) for key, item in value.items()}
    for provider_key, model_key in MODEL_FIELD_PAIRS:
        if provider_key in result and model_key in result:
            result.pop(provider_key, None)
            result.pop(model_key, None)
    return result


def mask_model_fields_for_role(ctx: AppContext, role: str, surface: str, payload: Any) -> Any:
    if role == AUTH_ROLE_ADMIN:
        return payload
    if policy_mode(ctx, surface) == "hide":
        return _hide_payload_model_fields(payload)
    return _mask_payload_value(ctx, surface, payload)


def mask_model_fields_under_keys_for_role(ctx: AppContext, role: str, surface: str, payload: Any, keys: set[str]) -> Any:
    if role == AUTH_ROLE_ADMIN:
        return payload
    if isinstance(payload, list):
        return [mask_model_fields_under_keys_for_role(ctx, role, surface, item, keys) for item in payload]
    if not isinstance(payload, dict):
        return payload
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if key in keys:
            result[key] = mask_model_fields_for_role(ctx, role, surface, value)
        else:
            result[key] = mask_model_fields_under_keys_for_role(ctx, role, surface, value, keys)
    return result
