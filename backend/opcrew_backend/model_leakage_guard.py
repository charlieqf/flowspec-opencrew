from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .context import AppContext
from .model_policy import MODEL_FIELD_PAIRS
from .routes.auth import ADMIN_ONLY_PATH_PREFIXES, AUTH_ROLE_ADMIN, AUTH_ROLE_USER
from .services.tts_voice_aliases import alias_customer_tts_voices


def _provider_brand_token(name: str) -> str:
    return rf"(?<![a-z0-9]){re.escape(name)}(?:[_-][a-z0-9]+)*(?=$|[^a-z0-9])"


def _load_model_leakage_policy() -> dict[str, Any]:
    path = Path(__file__).with_name("model_leakage_policy.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("version") or 0) < 1:
        raise RuntimeError("model leakage policy must be a versioned JSON object")
    provider_brands = {str(name).strip() for name in payload.get("provider_brands") or [] if str(name).strip()}
    egress_provider_brands = {str(name).strip() for name in payload.get("egress_provider_brands") or [] if str(name).strip()}
    if not provider_brands or not egress_provider_brands or not payload.get("model_patterns"):
        raise RuntimeError("model leakage policy must define provider, egress, and model deny patterns")
    if not egress_provider_brands.issubset(provider_brands):
        raise RuntimeError("model leakage egress provider brands must be a subset of bundle provider brands")
    return payload


MODEL_LEAKAGE_POLICY = _load_model_leakage_policy()
MODEL_LEAKAGE_BRAND_TERMS = tuple(
    _provider_brand_token(str(name))
    for name in MODEL_LEAKAGE_POLICY.get("egress_provider_brands") or []
) + tuple(str(pattern) for pattern in MODEL_LEAKAGE_POLICY.get("provider_literal_patterns") or [])
MODEL_LEAKAGE_DENY_TERMS = (
    *tuple(str(pattern) for pattern in MODEL_LEAKAGE_POLICY.get("domain_patterns") or []),
    *MODEL_LEAKAGE_BRAND_TERMS,
    *tuple(str(pattern) for pattern in MODEL_LEAKAGE_POLICY.get("model_patterns") or []),
    *tuple(re.escape(str(field)) for field in MODEL_LEAKAGE_POLICY.get("forbidden_fields") or []),
)

MODEL_LEAKAGE_DENY_RE = re.compile("(?i)(" + "|".join(MODEL_LEAKAGE_DENY_TERMS) + ")")
MODEL_LEAKAGE_BRAND_RE = re.compile("(?i)(" + "|".join(MODEL_LEAKAGE_BRAND_TERMS) + ")")
INTERNAL_PROMPT_PROVIDER_RE = re.compile(
    "(?i)("
    + "|".join(
        rf"(?<![a-z0-9]){name}(?:[_-][a-z0-9]+)*(?=$|[^a-z0-9])"
        for name in (
            "aliyun",
            "bytedance",
            "dashscope",
            "doubao",
            "gemini",
            "gpt",
            "grok",
            "hailuo",
            "heygen",
            "kling",
            "minimax",
            "openai",
            "openrouter",
            "qwen",
            "seedance",
            "seedream",
            "tongyi",
            "volcengine",
            "wan",
            "wanx",
            "xai",
        )
    )
    + ")"
)
CUSTOMER_EGRESS_SURFACE = "customer.egress"
CUSTOMER_EGRESS_PRESERVED_PATH_KEYS: set[str] = set()
LOCAL_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:/Users/[^/\\\s\"']+|/home/[^/\\\s\"']+|[A-Z]:[/\\]+Users[/\\]+[^/\\\s\"']+)[/\\]")

CUSTOMER_EGRESS_KEY_DENYLIST = {
    "argv",
    "provider_result",
    "provider_response",
    "provider_request",
    "provider_payload",
    "provider_metadata",
    "provider_snapshot",
    "provider_raw",
    "agent_snapshot",
    "generation_model",
    "gemini_meta",
    "docs_url",
    "heygen",
    "heygen_asset_id",
    "heygen_audio_asset_id",
    "provider_label_real",
    "model_label_real",
    "raw_response",
    "raw_request",
    "backups",
    "cleanup_actions",
    "created_files",
    "model_calls",
    "provider_profile",
    "prepared_directories",
    "reads_session_context",
    "requires_database",
    "requires_model_calls",
    "result_path",
    "script_path",
    "source_template_path",
    "source_plan_hash",
    "stderr_tail",
    "stdout_tail",
    "synth_id",
    "synthid",
    "synthid_metadata",
    "synthid_watermark",
    "template_blocks",
    "template_name",
    "template_snapshot_chars",
    "template_source",
    "writes_session_context",
}

CUSTOMER_EGRESS_FREE_TEXT_KEYS = {
    "caption",
    "content",
    "description",
    "label",
    "message",
    "name",
    "notes",
    "prompt",
    "query",
    "script",
    "summary",
    "text",
    "title",
}

CUSTOMER_EGRESS_PROVIDER_KEYS = {
    "provider",
    "providerid",
    "providername",
    "model_provider",
    "prompt_model_provider",
    "run_model_provider",
    "skill_model_provider",
    "used_prompt_model_provider",
    "used_run_model_provider",
    "video_provider",
    "image_provider",
    "lipsync_provider",
    "tts_provider",
}

CUSTOMER_EGRESS_MODEL_KEYS = {
    "model",
    "model_id",
    "modelid",
    "modelname",
    "prompt_model_id",
    "run_model_id",
    "skill_model_id",
    "used_prompt_model_id",
    "used_run_model_id",
    "video_model",
    "image_model",
    "lipsync_model",
    "tts_model",
}


INTERNAL_PROMPT_PACKAGE_MARKER_KEYS = {
    "provider_profile",
    "template_blocks",
    "template_snapshot_chars",
    "template_source",
}


CUSTOMER_EGRESS_PROVIDER_VALUES = {
    "aliyun",
    "bfl",
    "bytedance",
    "chanjing",
    "cosyvoice",
    "dashscope",
    "deepseek",
    "doubao",
    "gemini",
    "google",
    "grok",
    "hailuo",
    "heygen",
    "kling",
    "minimax",
    "openai",
    "openrouter",
    "qwen",
    "seedance",
    "seedream",
    "sync.so",
    "tongyi",
    "volc",
    "volcengine",
    "wan",
    "wanx",
    "xai",
}

EXCLUDED_API_PREFIXES = (
    "/api/auth/",
    "/api/health",
)

FILE_DOWNLOAD_RE = re.compile(
    r"^/api/(?:"
    r"session-tasks/\d+/(?:raw|thumbnail)/|"
    r"session-tasks/\d+/files\.zip$|"
    r"session-download/\d+/.+|"
    r"sessions/\d+/files/.+|"
    r"session-share/[^/]+/files/.+"
    r")"
)


def customer_egress_role_from_scope(scope: Scope) -> str:
    state = scope.get("state") if isinstance(scope.get("state"), dict) else {}
    role = str((state or {}).get("opencrew_auth_role") or "").strip()
    return role if role in {AUTH_ROLE_ADMIN, AUTH_ROLE_USER} else AUTH_ROLE_USER


def customer_egress_role_from_request(request: Any) -> str:
    role = str(getattr(getattr(request, "state", None), "opencrew_auth_role", "") or "").strip()
    return role if role in {AUTH_ROLE_ADMIN, AUTH_ROLE_USER} else AUTH_ROLE_USER


def should_filter_customer_egress_path(path: str) -> bool:
    if not path.startswith("/api/"):
        return False
    if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in EXCLUDED_API_PREFIXES):
        return False
    if any(path.startswith(prefix) for prefix in ADMIN_ONLY_PATH_PREFIXES):
        return False
    if FILE_DOWNLOAD_RE.match(path):
        return False
    return True


def _drop_denylist_keys(value: Any) -> Any:
    if isinstance(value, list):
        return [_drop_denylist_keys(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text.lower() in CUSTOMER_EGRESS_KEY_DENYLIST:
            continue
        result[key_text] = _drop_denylist_keys(item)
    return result


def _scrub_local_absolute_paths(value: Any, parent_key: str = "") -> Any:
    if isinstance(value, list):
        return [_scrub_local_absolute_paths(item, parent_key) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_local_absolute_paths(item, str(key).lower()) for key, item in value.items()}
    if isinstance(value, str) and parent_key not in CUSTOMER_EGRESS_PRESERVED_PATH_KEYS and LOCAL_ABSOLUTE_PATH_RE.search(value):
        candidate = value.strip()
        if candidate[:1] in {"{", "["}:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, (dict, list)):
                return value
        return ""
    return value


def _is_internal_prompt_package(value: dict[str, Any]) -> bool:
    schema_version = str(value.get("schema_version") or "").lower()
    if "image_prompt" in schema_version or "video_prompt" in schema_version:
        return True
    return any(key in value for key in INTERNAL_PROMPT_PACKAGE_MARKER_KEYS)


def _scrub_internal_prompt_package_strings(value: Any) -> Any:
    if isinstance(value, list):
        return [_scrub_internal_prompt_package_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_internal_prompt_package_strings(item) for key, item in value.items()}
    if isinstance(value, str):
        return INTERNAL_PROMPT_PROVIDER_RE.sub("[model]", value)
    return value


def _scrub_internal_prompt_packages(value: Any) -> Any:
    if isinstance(value, list):
        return [_scrub_internal_prompt_packages(item) for item in value]
    if not isinstance(value, dict):
        return value
    if _is_internal_prompt_package(value):
        return _scrub_internal_prompt_package_strings(value)
    return {key: _scrub_internal_prompt_packages(item) for key, item in value.items()}


def _scrub_strings(value: Any) -> Any:
    return _scrub_strings_for_key(value, "")


def _scrub_strings_for_key(value: Any, parent_key: str) -> Any:
    if isinstance(value, list):
        return [_scrub_strings_for_key(item, parent_key) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_strings_for_key(item, str(key).lower()) for key, item in value.items()}
    if isinstance(value, str):
        if parent_key in CUSTOMER_EGRESS_PRESERVED_PATH_KEYS:
            return value
        if parent_key in CUSTOMER_EGRESS_FREE_TEXT_KEYS:
            return MODEL_LEAKAGE_BRAND_RE.sub("[model]", value)
        return MODEL_LEAKAGE_DENY_RE.sub("[model]", value)
    return value


def _sanitize_customer_value(value: Any) -> Any:
    value = _scrub_internal_prompt_packages(value)
    value = _drop_denylist_keys(value)
    value = _scrub_local_absolute_paths(value)
    value = _mask_customer_model_fields(value)
    return _scrub_strings(value)


def _scrub_embedded_json_strings(value: Any) -> Any:
    if isinstance(value, list):
        return [_scrub_embedded_json_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_embedded_json_strings(item) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if not candidate or candidate[0] not in "{[":
        return value
    try:
        parsed = json.loads(candidate)
    except Exception:
        return value
    sanitized = _scrub_embedded_json_strings(_sanitize_customer_value(parsed))
    return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))


def _norm_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _looks_like_provider_leak(value: Any) -> bool:
    text = _norm_token(value)
    return bool(text) and (text in CUSTOMER_EGRESS_PROVIDER_VALUES or bool(MODEL_LEAKAGE_DENY_RE.search(text)))


def _looks_like_model_leak(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and bool(MODEL_LEAKAGE_DENY_RE.search(text))


def _apply_pair_mask(result: dict[str, Any], provider_key: str, model_key: str) -> None:
    result[provider_key], result[model_key] = "", ""
    if provider_key == "providerID" and "providerName" in result:
        result["providerName"] = ""
    if model_key == "modelID" and "modelName" in result:
        result["modelName"] = ""


def _mask_customer_model_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_mask_customer_model_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _mask_customer_model_fields(item) for key, item in value.items()}
    for provider_key, model_key in MODEL_FIELD_PAIRS:
        if provider_key not in result or model_key not in result:
            continue
        if not (_norm_token(result.get(provider_key)) or _norm_token(result.get(model_key))):
            continue
        if _looks_like_provider_leak(result.get(provider_key)) or _looks_like_model_leak(result.get(model_key)):
            _apply_pair_mask(result, provider_key, model_key)
    for key, item in list(result.items()):
        normalized_key = str(key).lower()
        if normalized_key in CUSTOMER_EGRESS_PROVIDER_KEYS and _looks_like_provider_leak(item):
            result[key] = ""
        elif normalized_key in CUSTOMER_EGRESS_MODEL_KEYS and _looks_like_model_leak(item):
            result[key] = ""
    return result


def sanitize_customer_payload(ctx: AppContext, role: str, value: Any, *, surface: str = CUSTOMER_EGRESS_SURFACE) -> Any:
    if role == AUTH_ROLE_ADMIN:
        return value
    value = alias_customer_tts_voices(ctx, value)
    del surface
    return _scrub_embedded_json_strings(_sanitize_customer_value(value))


def sanitize_customer_text(value: str) -> str:
    return MODEL_LEAKAGE_DENY_RE.sub("[model]", value)


def _is_json_content_type(content_type: str) -> bool:
    value = content_type.split(";", 1)[0].strip().lower()
    return value == "application/json" or value.endswith("+json")


def _is_sse_content_type(content_type: str) -> bool:
    return content_type.split(";", 1)[0].strip().lower() == "text/event-stream"


def _allows_json_fallback_content_type(content_type: str) -> bool:
    value = content_type.split(";", 1)[0].strip().lower()
    return value in {"", "text/plain"}


def _sanitize_json_body(ctx: AppContext, role: str, body: bytes, *, scrub_invalid: bool = True) -> bytes:
    try:
        parsed = json.loads(body.decode("utf-8") or "null")
    except Exception:
        if not scrub_invalid:
            return body
        try:
            return sanitize_customer_text(body.decode("utf-8")).encode("utf-8")
        except Exception:
            return b'"[filtered]"'
    sanitized = sanitize_customer_payload(ctx, role, parsed)
    return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sanitize_sse_frame(ctx: AppContext, role: str, frame: bytes) -> bytes:
    if not frame:
        return frame
    try:
        text = frame.decode("utf-8")
    except Exception:
        return sanitize_customer_text(frame.decode("utf-8", "ignore")).encode("utf-8")
    lines = text.splitlines()
    data_values: list[str] = []
    passthrough: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            data_values.append(value)
        else:
            passthrough.append(line)
    if not data_values:
        return (text + "\n\n").encode("utf-8")
    data_text = "\n".join(data_values)
    try:
        parsed = json.loads(data_text)
        sanitized_data_lines = [json.dumps(sanitize_customer_payload(ctx, role, parsed), ensure_ascii=True, separators=(",", ":"))]
    except Exception:
        sanitized_data_lines = sanitize_customer_text(data_text).split("\n")
    output = [*passthrough, *(f"data: {line}" for line in sanitized_data_lines)]
    return ("\n".join(output) + "\n\n").encode("utf-8")


def _sanitize_sse_bytes(ctx: AppContext, role: str, chunk: bytes, pending: bytes, *, final: bool = False) -> tuple[bytes, bytes]:
    data = pending + chunk
    output = bytearray()
    while True:
        marker = data.find(b"\n\n")
        if marker < 0:
            break
        frame = data[:marker]
        output.extend(_sanitize_sse_frame(ctx, role, frame))
        data = data[marker + 2 :]
    if final and data:
        output.extend(_sanitize_sse_frame(ctx, role, data))
        data = b""
    return bytes(output), data


class CustomerEgressSanitizerMiddleware:
    def __init__(self, app: ASGIApp, ctx: AppContext) -> None:
        self.app = app
        self.ctx = ctx

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not should_filter_customer_egress_path(str(scope.get("path") or "")):
            await self.app(scope, receive, send)
            return

        initial_message: Message | None = None
        mode = ""
        should_filter = False
        role = AUTH_ROLE_USER
        body_parts: list[bytes] = []
        sse_pending = b""
        scrub_invalid_json = True

        async def send_with_sanitizer(message: Message) -> None:
            nonlocal initial_message, mode, should_filter, role, sse_pending, scrub_invalid_json
            if message["type"] == "http.response.start":
                initial_message = message
                role = customer_egress_role_from_scope(scope)
                headers = Headers(raw=message.get("headers", []))
                content_type = headers.get("content-type", "")
                is_json = _is_json_content_type(content_type)
                is_sse = _is_sse_content_type(content_type)
                is_json_fallback = _allows_json_fallback_content_type(content_type)
                should_filter = (
                    role != AUTH_ROLE_ADMIN
                    and int(message.get("status", 200) or 200) >= 200
                    and "content-encoding" not in headers
                    and (is_json or is_sse or is_json_fallback)
                )
                if is_json or is_json_fallback:
                    mode = "json"
                    scrub_invalid_json = is_json
                elif is_sse:
                    mode = "sse"
                if not should_filter:
                    await send(message)
                elif mode == "sse":
                    headers_mut = MutableHeaders(raw=message["headers"])
                    if "content-length" in headers_mut:
                        del headers_mut["content-length"]
                    await send(message)
                return

            if message["type"] != "http.response.body" or not should_filter:
                await send(message)
                return

            if mode == "sse":
                chunk, sse_pending = _sanitize_sse_bytes(
                    self.ctx,
                    role,
                    message.get("body", b""),
                    sse_pending,
                    final=not message.get("more_body", False),
                )
                await send({**message, "body": chunk})
                return

            body_parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return

            assert initial_message is not None
            raw_body = b"".join(body_parts)
            if not raw_body:
                await send(initial_message)
                await send(message)
                return
            body = _sanitize_json_body(self.ctx, role, raw_body, scrub_invalid=scrub_invalid_json)
            headers_mut = MutableHeaders(raw=initial_message["headers"])
            headers_mut["Content-Length"] = str(len(body))
            await send(initial_message)
            await send({**message, "body": body})

        await self.app(scope, receive, send_with_sanitizer)
