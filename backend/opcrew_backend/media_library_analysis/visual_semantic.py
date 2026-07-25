from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from fastapi import HTTPException

from ..adapters.opencode import OpenCodeSessionClient
from ..context import now_ms
from ..media_library_features import (
    media_library_feature_state,
    require_media_library_feature,
)
from ..model_policy import (
    SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC,
    resolve_prompt_model_for_role,
    surface_policy,
)
from ..routes.auth import AUTH_ROLE_USER
from ..services.opencode_runtime import opencode_client_for_context
from ..tool_sessions import (
    PrepareInputFile,
    PrepareSessionVariablesInput,
    ToolSessionRunner,
    ToolSessionService,
)
from ..tool_sessions.registry_normalizer import normalize_registry_file
from ..tool_sessions.schemas import PromptManifest, ToolResult
from .contracts import result_hash, sha256_file
from .lifecycle import finalize_analysis_tool_session, result_sync_error
from .run_repository import AnalysisRunRepository
from .visual_semantic_contracts import (
    CANDIDATE_SCHEMA_VERSION,
    INPUT_KEYFRAMES_REL,
    INPUT_MANIFEST_REL,
    INPUT_SCHEMA_VERSION,
    INPUT_STRUCTURE_SEGMENTS_REL,
    MANIFEST_PATH,
    QUALITY_PATH,
    RESULT_PATH,
    RESULT_SCHEMA_VERSION,
    SAMPLING_STRATEGY,
    VisualSemanticValidationError,
    publish_visual_semantic_contract,
    validate_visual_semantic_item,
)


OPENCREW_ROOT = Path(__file__).resolve().parents[3]
OPEN_CUT_REGISTRY = OPENCREW_ROOT / "ToolLibrary" / "OpenCut_V1" / "tool_registry.json"
WORKFLOW_ID = "open-cut-v1-visual-semantic"
TOOL_ID = "03_03"
STEP_ID = "S1"
PROMPT_VERSION_DEFAULT = "visual_semantic_prompt_v3"
TOTAL_STEPS = 2
SAFE_KEYFRAME_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
DESCRIPTION_FIELDS = {
    "visual_summary",
    "people",
    "objects",
    "scene",
    "action",
    "keywords",
    "claim_evidence",
    "confidence",
    "needs_review",
}
AUTHORITATIVE_FIELDS = {
    "fragment_id",
    "start_ms",
    "end_ms",
    "duration_ms",
    "keyframe_refs",
}
DISABLED_MODEL_TOOLS = {
    name: False
    for name in (
        "bash",
        "edit",
        "glob",
        "grep",
        "list",
        "read",
        "skill",
        "task",
        "todowrite",
        "webfetch",
        "websearch",
        "write",
    )
}
LOCAL_MODEL_PROVIDERS = {
    "local",
    "ollama",
    "lmstudio",
    "lm-studio",
    "llamacpp",
    "llama.cpp",
    "vllm",
}
SYSTEM_PROMPT = """You describe exactly one video fragment from four ordered sampled frames.
Return one strict JSON object and no markdown. Allowed keys are:
visual_summary, people, objects, scene, action, keywords, claim_evidence,
confidence, needs_review.
The object must have exactly this shape and these JSON types:
{
  "visual_summary": "short visible summary or null",
  "people": ["short anonymous visible person description"],
  "objects": ["short visible object description"],
  "scene": "short visible scene description or null",
  "action": null,
  "keywords": ["short visible keyword"],
  "claim_evidence": {
    "people": ["the supplied keyframe id, only when people is non-empty"],
    "objects": ["the supplied keyframe id, only when objects is non-empty"],
    "scene": ["the supplied keyframe id, only when scene is non-null"],
    "action": []
  },
  "confidence": 0.0,
  "needs_review": false
}
people, objects, and keywords are arrays of strings, never arrays of objects.
Evidence ids belong only inside claim_evidence; do not attach evidence fields
to individual people, objects, or keywords.
Use concise Simplified Chinese for visual_summary, people, objects, scene, and
keywords. Preserve all four supplied keyframe ids exactly; do not translate or
alter stable ids or evidence references. Cite only the sampled frame or frames
that directly support each people, objects, or scene field.
Use only visible evidence from these four images. Never identify a real person or
infer race, ethnicity, health, disability, politics, religion, sexual
orientation, intent, causality, off-screen facts, or dialogue content.
These are sparse still frames, not continuous video evidence, so action must be
null and claim_evidence.action must be []. You may objectively describe visible
state differences between samples, but never infer the missing action, cause, or
intent. Each non-empty people, objects, or scene claim must cite one or more of
the supplied keyframe ids. Unknown values use null or [].
confidence is a number from 0 to 1 and needs_review is boolean."""


def _repair_instruction(error_code: str) -> str:
    field_hint = {
        "visual_semantic_people_invalid": (
            "people must be an array of short anonymous strings, never " "objects; put the supplied keyframe id only in " "claim_evidence.people."
        ),
        "visual_semantic_objects_invalid": (
            "objects must be an array of short strings, never objects; put " "the supplied keyframe id only in claim_evidence.objects."
        ),
        "visual_semantic_keywords_invalid": ("keywords must be an array of short strings, never objects."),
        "visual_semantic_claim_evidence_invalid": (
            "claim_evidence must contain exactly people, objects, scene, " "and action; every value is an array of keyframe-id strings."
        ),
    }.get(str(error_code or ""), "")
    prefix = f"{field_hint} " if field_hint else ""
    return (
        prefix
        + "Return one corrected full object using exactly the JSON shape and "
        "types in the system instruction. Keep all user-facing natural-language "
        "fields in concise Simplified Chinese."
    )


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _extract_json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = value.strip("`").strip()
        if value.lower().startswith("json"):
            value = value[4:].strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("visual_semantic_model_json_missing")
        parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("visual_semantic_model_json_invalid")
    if isinstance(parsed.get("item"), dict):
        return dict(parsed["item"])
    return parsed


def _last_completed_assistant(messages: list[dict[str, Any]], started_after: int) -> str | None:
    for message in reversed(messages):
        info = message.get("info") or {}
        if info.get("role") != "assistant":
            continue
        completed = int(((info.get("time") or {}).get("completed") or 0) or 0)
        if completed < started_after:
            continue
        text = "\n".join(
            str(part.get("text") or "").strip()
            for part in (message.get("parts") or [])
            if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text") or "").strip()
        ).strip()
        if text:
            return text
    return None


def _catalog_input_modalities(model: Mapping[str, Any]) -> list[str]:
    supported = {"text", "audio", "image", "video", "pdf"}
    values: list[Any] = []
    modalities = model.get("modalities")
    if isinstance(modalities, dict) and isinstance(modalities.get("input"), list):
        values.extend(modalities["input"])
    capabilities = model.get("capabilities")
    if isinstance(capabilities, dict):
        capability_input = capabilities.get("input")
        if isinstance(capability_input, list):
            values.extend(capability_input)
        elif isinstance(capability_input, dict):
            values.extend(name for name, enabled in capability_input.items() if enabled is True)
    normalized: list[str] = []
    for value in values:
        item = str(value or "").strip().lower()
        if item in supported and item not in normalized:
            normalized.append(item)
    return normalized


def _catalog_max_images_per_request(model: Mapping[str, Any]) -> int:
    candidates: list[Any] = [
        model.get("max_images_per_request"),
        model.get("maxImagesPerRequest"),
        model.get("max_input_images"),
    ]
    capabilities = model.get("capabilities")
    if isinstance(capabilities, dict):
        candidates.extend(
            [
                capabilities.get("max_images_per_request"),
                capabilities.get("maxImagesPerRequest"),
                capabilities.get("max_input_images"),
            ]
        )
        image = capabilities.get("image")
        if isinstance(image, dict):
            candidates.extend(
                [
                    image.get("max_count"),
                    image.get("maxCount"),
                    image.get("max_images_per_request"),
                ]
            )
    for value in candidates:
        if isinstance(value, bool):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 0


def _model_catalog(client: OpenCodeSessionClient) -> dict[str, Any]:
    payload = client.providers()
    connected = {str(item) for item in (payload.get("connected") or []) if str(item).strip()}
    defaults = payload.get("default") or {}
    items: list[dict[str, Any]] = []
    default_model = {"providerID": "", "modelID": ""}
    for provider in payload.get("all") or []:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id") or "").strip()
        if not provider_id or provider_id not in connected:
            continue
        models = provider.get("models") or {}
        if not isinstance(models, dict):
            continue
        for raw in models.values():
            model = raw if isinstance(raw, dict) else {}
            model_id = str(model.get("id") or "").strip()
            if not model_id:
                continue
            items.append(
                {
                    "providerID": provider_id,
                    "providerName": str(provider.get("name") or provider_id),
                    "modelID": model_id,
                    "modelName": str(model.get("name") or model_id),
                    "inputModalities": _catalog_input_modalities(model),
                    "maxImagesPerRequest": (
                        _catalog_max_images_per_request(model)
                    ),
                }
            )
        configured = str(defaults.get(provider_id) or "").strip()
        if configured and not default_model["providerID"]:
            default_model = {
                "providerID": provider_id,
                "modelID": configured,
            }
    if not default_model["providerID"] and items:
        default_model = {
            "providerID": str(items[0]["providerID"]),
            "modelID": str(items[0]["modelID"]),
        }
    return {"items": items, "default_model": default_model}


def _model_supports_four_images(
    *,
    catalog: Mapping[str, Any],
    model: Mapping[str, str],
) -> bool:
    provider_id = str(model.get("providerID") or "")
    model_id = str(model.get("modelID") or "")
    for item in catalog.get("items") or []:
        if not isinstance(item, dict):
            continue
        if (
            str(item.get("providerID") or "") == provider_id
            and str(item.get("modelID") or "") == model_id
        ):
            try:
                if int(item.get("maxImagesPerRequest") or 0) >= 4:
                    return True
            except (TypeError, ValueError):
                pass
    configured = {
        value.strip()
        for value in os.environ.get(
            "OPENCREW_MEDIA_LIBRARY_VISUAL_MULTI_IMAGE_MODELS",
            "",
        ).split(",")
        if value.strip()
    }
    return (
        f"{provider_id}/{model_id}" in configured
        or f"{provider_id}/*" in configured
    )


def _is_local_model_provider(provider: str, endpoint_url: str = "") -> bool:
    configured = {item.strip().lower() for item in os.environ.get("OPENCREW_LOCAL_MODEL_PROVIDERS", "").split(",") if item.strip()}
    if provider.strip().lower() not in (LOCAL_MODEL_PROVIDERS | configured):
        return False
    hostname = (urlparse(endpoint_url).hostname or "").strip().lower()
    configured_hosts = {item.strip().lower() for item in os.environ.get("OPENCREW_LOCAL_MODEL_ENDPOINT_HOSTS", "").split(",") if item.strip()}
    if hostname in configured_hosts or hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _int_setting(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _read_bounded_image(path: Path) -> tuple[str, bytes]:
    maximum = _int_setting(
        "OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_KEYFRAME_BYTES",
        20 * 1024 * 1024,
    )
    size = path.stat().st_size
    if size <= 0 or size > maximum:
        raise ValueError("visual_semantic_keyframe_size_invalid")
    with path.open("rb") as handle:
        header = handle.read(16)
        handle.seek(0)
        content = handle.read(maximum + 1)
    if len(content) != size or len(content) > maximum:
        raise ValueError("visual_semantic_keyframe_size_invalid")
    if header.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif header.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("visual_semantic_keyframe_format_invalid")
    return mime, content


def _read_four_bounded_images(
    paths: list[Path],
) -> list[tuple[Path, str, bytes]]:
    if len(paths) != 4:
        raise ValueError("visual_semantic_four_keyframes_required")
    maximum_total = _int_setting(
        "OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_FRAGMENT_IMAGE_BYTES",
        32 * 1024 * 1024,
    )
    sizes = [path.stat().st_size for path in paths]
    encoded_size = sum(
        4 * ((size + 2) // 3)
        + len(
            json.dumps(
                {
                    "type": "file",
                    "mime": "image/webp",
                    "filename": path.name,
                    "url": "data:image/webp;base64,",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        for path, size in zip(paths, sizes, strict=True)
    )
    # Include the surrounding list and a conservative allowance for provider
    # request metadata. The configured ceiling applies to the transmitted
    # base64/JSON payload, not only to the smaller source files on disk.
    estimated_payload_size = encoded_size + 4096
    if estimated_payload_size > maximum_total:
        raise VisualSemanticBlocked(
            "visual_semantic_keyframe_payload_too_large",
            "当前画面片段的四张采样图总负载超过限制。",
            "请调整受控截图大小或由管理员调整四图负载上限。",
        )
    return [(path, *_read_bounded_image(path)) for path in paths]


class VisualSemanticBlocked(RuntimeError):
    def __init__(self, code: str, user_message: str, suggested_action: str) -> None:
        self.code = code
        self.user_message = user_message
        self.suggested_action = suggested_action
        super().__init__(user_message)

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "user_message": self.user_message,
            "suggested_action": self.suggested_action,
        }


class VisualSemanticModelOutputError(ValueError):
    def __init__(self, raw_text: str) -> None:
        self.raw_text = raw_text
        super().__init__("visual_semantic_model_json_invalid")


class VisualSemanticToolAdapter:
    _model_semaphore = threading.BoundedSemaphore(_int_setting("OPENCREW_MEDIA_LIBRARY_VISUAL_MODEL_CONCURRENCY", 1))

    def __init__(
        self,
        *,
        ctx: Any,
        session: dict[str, Any],
        asset: dict[str, Any],
        analysis_run_id: str,
        visual_structure_run_id: str,
        visual_structure_result_hash: str,
        visual_prompt_version: str,
        model_config_id: str,
        allow_cloud_visual_data_transfer: bool,
    ) -> None:
        self.ctx = ctx
        self.session = session
        self.asset = asset
        self.analysis_run_id = analysis_run_id
        self.visual_structure_run_id = visual_structure_run_id
        self.visual_structure_result_hash = visual_structure_result_hash
        self.visual_prompt_version = visual_prompt_version
        self.model_config_id = model_config_id
        self.allow_cloud_visual_data_transfer = allow_cloud_visual_data_transfer
        self.cache_root = Path(str(ctx.data_dir)) / "cache" / "media_library_visual_semantic"

    def _resolve_model(
        self,
    ) -> tuple[OpenCodeSessionClient, dict[str, str], dict[str, Any]]:
        policy = surface_policy(self.ctx, SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC)
        required_modalities = {str(item).strip().lower() for item in (policy.get("required_input_modalities") or []) if str(item).strip()}
        if str(policy.get("mode") or "").strip().lower() != "alias" or not bool(policy.get("alias_only")) or "image" not in required_modalities:
            raise VisualSemanticBlocked(
                "visual_model_policy_invalid",
                "视觉语义模型策略缺少只读别名或图像输入门禁。",
                "请由管理员补齐 media_library.visual_semantic 策略。",
            )
        try:
            client = opencode_client_for_context(
                self.ctx,
                self.session,
                "视觉语义模型服务尚未配置。",
            )
            catalog = _model_catalog(client)
            model, _masked = resolve_prompt_model_for_role(
                self.ctx,
                AUTH_ROLE_USER,
                SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC,
                catalog,
                "",
                "",
                "Visual semantic",
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            raise VisualSemanticBlocked(
                str(detail.get("code") or "visual_model_configuration_unavailable"),
                str(detail.get("user_message") or "没有可用且支持图像输入的已批准视觉模型。"),
                str(detail.get("suggested_action") or "请由管理员配置视觉语义模型别名后重试。"),
            ) from exc
        except Exception as exc:
            raise VisualSemanticBlocked(
                "visual_model_configuration_unavailable",
                "视觉语义模型服务尚未配置或不可用。",
                "请完成 OpenCode 与已批准图像模型配置后重试。",
            ) from exc
        if not _model_supports_four_images(catalog=catalog, model=model):
            raise VisualSemanticBlocked(
                "visual_model_multi_image_unsupported",
                "当前视觉模型未证明支持在一次请求中接收四张图片。",
                "请选择已通过四图 capability smoke 的模型配置后重试。",
            )
        provider = str(model.get("providerID") or "")
        if not _is_local_model_provider(provider, str(getattr(client, "base_url", "") or "")) and not self.allow_cloud_visual_data_transfer:
            raise VisualSemanticBlocked(
                "cloud_visual_data_transfer_not_authorized",
                "当前视觉模型需要发送受控 Keyframe，但尚未获得图像外发授权。",
                "明确勾选图像外发授权，或配置本地视觉模型后重试。",
            )
        return client, model, catalog

    def _cache_key(
        self,
        ordered_image_sha256: list[str],
        model: Mapping[str, str],
    ) -> str:
        if len(ordered_image_sha256) != 4:
            raise ValueError("visual_semantic_four_keyframes_required")
        return result_hash(
            {
                "ordered_image_sha256": ordered_image_sha256,
                "sampling_strategy": SAMPLING_STRATEGY,
                "visual_prompt_version": self.visual_prompt_version,
                "model_config_id": self.model_config_id,
                "resolved_model_target_hash": hashlib.sha256((f"{model.get('providerID', '')}\0" f"{model.get('modelID', '')}").encode("utf-8")).hexdigest(),
                "schema_version": RESULT_SCHEMA_VERSION,
            }
        )

    def _cache_read(self, cache_key: str, authoritative: dict[str, Any]) -> dict[str, Any] | None:
        path = self.cache_root / f"{cache_key}.json"
        if not path.is_file():
            return None
        try:
            payload = _read_object(path)
            if payload.get("cache_key") != cache_key:
                return None
            description = payload.get("description")
            if not isinstance(description, dict):
                return None
            return self._validate_description(authoritative, description)
        except Exception:
            return None

    def _cache_write(self, cache_key: str, validated_item: dict[str, Any]) -> None:
        description = {field: validated_item.get(field) for field in DESCRIPTION_FIELDS}
        _write_json(
            self.cache_root / f"{cache_key}.json",
            {
                "schema_version": "media_library_visual_semantic_cache_v2",
                "cache_key": cache_key,
                "description": description,
            },
        )

    @staticmethod
    def _validate_description(authoritative: dict[str, Any], description: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(description)
        expected = {
            "fragment_id": authoritative["fragment_id"],
            "start_ms": authoritative["start_ms"],
            "end_ms": authoritative["end_ms"],
            "duration_ms": authoritative["duration_ms"],
            "keyframe_refs": [
                keyframe["keyframe_id"]
                for keyframe in authoritative["keyframes"]
            ],
        }
        for field in AUTHORITATIVE_FIELDS:
            if field in raw and raw[field] != expected[field]:
                raise VisualSemanticValidationError(f"visual_semantic_{field}_modified")
        raw["action"] = None
        evidence = raw.get("claim_evidence")
        if isinstance(evidence, dict):
            evidence = dict(evidence)
            evidence["action"] = []
            raw["claim_evidence"] = evidence
        merged = {**raw, **expected}
        return validate_visual_semantic_item(
            authoritative_item=authoritative,
            candidate_item=merged,
            sampling_strategy=SAMPLING_STRATEGY,
        )

    def _model_call(
        self,
        *,
        client: OpenCodeSessionClient,
        model_session_id: str,
        model: dict[str, str],
        authoritative: dict[str, Any],
        image_paths: list[Path],
        repair_error: str = "",
        prior_candidate: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        keyframe_ids = [
            str(keyframe["keyframe_id"])
            for keyframe in authoritative["keyframes"]
        ]
        if len(keyframe_ids) != 4:
            raise ValueError("visual_semantic_four_keyframes_required")
        prompt_payload: dict[str, Any] = {
            "fragment": {
                "keyframe_ids": keyframe_ids,
                "sampling_strategy": SAMPLING_STRATEGY,
            },
            "instruction": (
                "Describe only facts directly visible in the four supplied "
                "ordered sampled images. Do not infer continuous action."
            ),
        }
        if repair_error:
            prompt_payload["repair"] = {
                "validation_error": repair_error,
                "invalid_candidate": prior_candidate or {},
                "instruction": _repair_instruction(repair_error),
            }
        user_text = json.dumps(prompt_payload, ensure_ascii=False, separators=(",", ":"))
        images = _read_four_bounded_images(image_paths)
        image_parts = [
            {
                "type": "file",
                "mime": mime,
                "filename": image_path.name,
                "url": (
                    f"data:{mime};base64,"
                    f"{base64.b64encode(image_bytes).decode('ascii')}"
                ),
            }
            for image_path, mime, image_bytes in images
        ]
        started_at = now_ms()
        client.prompt_async(
            model_session_id,
            user_text,
            model=model,
            system=SYSTEM_PROMPT,
            tools=DISABLED_MODEL_TOOLS,
            parts=[
                {"type": "text", "text": user_text},
                *image_parts,
            ],
        )
        timeout_seconds = _int_setting("OPENCREW_MEDIA_LIBRARY_VISUAL_MODEL_TIMEOUT_SECONDS", 180)
        deadline = time.monotonic() + timeout_seconds
        assistant_text: str | None = None
        while time.monotonic() < deadline:
            assistant_text = _last_completed_assistant(
                client.messages(model_session_id, limit=40),
                started_at,
            )
            if assistant_text:
                break
            time.sleep(0.5)
        if not assistant_text:
            try:
                client.abort(model_session_id)
            except Exception:
                pass
            raise TimeoutError("visual_semantic_model_timeout")
        try:
            return _extract_json_object(assistant_text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise VisualSemanticModelOutputError(assistant_text) from exc

    def _record_usage(
        self,
        *,
        model: dict[str, str],
        fragment_id: str,
        call_index: int,
        started_at: int,
        status: str,
        error_code: str = "",
    ) -> tuple[str, str]:
        result = self.ctx.local_usage.record_with_result(
            provider=str(model.get("providerID") or ""),
            model_id=str(model.get("modelID") or ""),
            modality="image_to_text",
            proxy_policy="opencode_visual_semantic",
            status=status,
            task_id=self.asset.get("task_id"),
            attempt_id=self.analysis_run_id,
            step_id=TOOL_ID,
            idempotency_key=(f"media-library-visual-semantic:{self.analysis_run_id}:" f"{fragment_id}:{call_index}"),
            units={"image_count": 4, "model_call_count": 1},
            est_cost_micros=_int_setting(
                "OPENCREW_MEDIA_LIBRARY_VISUAL_EST_COST_PER_CALL_MICROS",
                1000,
            ),
            error_code=error_code,
            started_at=started_at,
            finished_at=now_ms(),
            provider_mode=(
                "local_box"
                if _is_local_model_provider(
                    str(model.get("providerID") or ""),
                    str((self.ctx.get_setting("opencode.base_url") if callable(getattr(self.ctx, "get_setting", None)) else "") or ""),
                )
                else "cloud"
            ),
        )
        return result.request_id, result.local_usage_id

    def run(
        self,
        *,
        tool: dict[str, Any],
        step: Any,
        paths: Any,
        tool_dir: Path,
    ) -> ToolResult:
        try:
            return self._run(tool=tool, step=step, paths=paths, tool_dir=tool_dir)
        except VisualSemanticBlocked as exc:
            return ToolResult(
                tool_id=TOOL_ID,
                tool_name=str(tool.get("name") or tool.get("tool_name") or ""),
                step_id=str(step.step_id),
                status="blocked",
                outputs={"error": exc.payload()},
                errors=[exc.user_message],
            )

    def _run(
        self,
        *,
        tool: dict[str, Any],
        step: Any,
        paths: Any,
        tool_dir: Path,
    ) -> ToolResult:
        structure = _read_object(paths.root / INPUT_STRUCTURE_SEGMENTS_REL)
        authoritative_items = [dict(item) for item in (structure.get("items") or []) if isinstance(item, dict)]
        max_scenes = _int_setting("OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_SCENES_PER_ASSET", 300)
        if not authoritative_items or len(authoritative_items) > max_scenes:
            raise VisualSemanticBlocked(
                "quota_exceeded",
                "Scene 数量超过本次视觉语义运行上限。",
                "减少 Scene 数量或由管理员调整视觉模型配额。",
            )

        client, model, _catalog = self._resolve_model()
        model_session = client.create_session(f"Media library visual semantic {self.analysis_run_id}")
        model_session_id = str(model_session["id"])
        self.ctx.media_analysis_run_repo.set_model_session(
            self.analysis_run_id,
            model_session_id=model_session_id,
            timestamp=now_ms(),
        )

        cache_hits = 0
        repairs = 0
        calls = 0
        model_call_audits: list[dict[str, Any]] = []
        validated_items: list[dict[str, Any]] = []
        max_calls = _int_setting(
            "OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_CALLS_PER_RUN",
            max_scenes * 2,
        )
        max_cost = _int_setting(
            "OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_EST_COST_MICROS",
            1_000_000,
        )
        cost_per_call = _int_setting(
            "OPENCREW_MEDIA_LIBRARY_VISUAL_EST_COST_PER_CALL_MICROS",
            1000,
        )
        for authoritative in authoritative_items:
            fragment_id = str(authoritative.get("fragment_id") or "")
            keyframes = authoritative.get("keyframes")
            if not isinstance(keyframes, list) or len(keyframes) != 4:
                raise ValueError(
                    f"visual_semantic_four_keyframes_required:{fragment_id}"
                )
            keyframe_ids: list[str] = []
            image_sha256: list[str] = []
            image_paths: list[Path] = []
            for keyframe in keyframes:
                if not isinstance(keyframe, dict):
                    raise ValueError(
                        f"visual_semantic_keyframe_invalid:{fragment_id}"
                    )
                keyframe_id = str(keyframe.get("keyframe_id") or "")
                expected_hash = str(keyframe.get("image_sha256") or "")
                image_matches = list(
                    (paths.root / INPUT_KEYFRAMES_REL).glob(
                        f"{keyframe_id}.*"
                    )
                )
                if len(image_matches) != 1:
                    raise ValueError(
                        "visual_semantic_keyframe_snapshot_missing:"
                        f"{keyframe_id}"
                    )
                image_path = image_matches[0]
                if sha256_file(image_path) != expected_hash:
                    raise ValueError(
                        "visual_semantic_keyframe_snapshot_changed:"
                        f"{keyframe_id}"
                    )
                keyframe_ids.append(keyframe_id)
                image_sha256.append(expected_hash)
                image_paths.append(image_path)
            cache_key = self._cache_key(image_sha256, model)
            cached = self._cache_read(cache_key, authoritative)
            if cached is not None:
                cache_hits += 1
                validated_items.append(cached)
                continue
            if calls + 1 > max_calls or (calls + 1) * cost_per_call > max_cost:
                raise VisualSemanticBlocked(
                    "quota_exceeded",
                    "本次视觉语义模型调用将超过已配置配额。",
                    "稍后重试，或由管理员调整调用次数和成本上限。",
                )
            candidate: dict[str, Any]
            parse_error: VisualSemanticValidationError | None = None
            call_started = now_ms()
            try:
                with self._model_semaphore:
                    try:
                        candidate = self._model_call(
                            client=client,
                            model_session_id=model_session_id,
                            model=model,
                            authoritative=authoritative,
                            image_paths=image_paths,
                        )
                    except VisualSemanticModelOutputError:
                        candidate = {}
                        parse_error = VisualSemanticValidationError("visual_semantic_model_json_invalid")
            except Exception as exc:
                calls += 1
                try:
                    self._record_usage(
                        model=model,
                        fragment_id=fragment_id,
                        call_index=calls,
                        started_at=call_started,
                        status="failed",
                        error_code=str(exc).split(":", 1)[0],
                    )
                except Exception:
                    pass
                raise
            calls += 1
            request_id, local_usage_id = self._record_usage(
                model=model,
                fragment_id=fragment_id,
                call_index=calls,
                started_at=call_started,
                status="ok",
            )
            model_call_audits.append(
                {
                    "fragment_id": fragment_id,
                    "keyframe_ids": keyframe_ids,
                    "image_count": 4,
                    "request_id": request_id,
                    "local_usage_id": local_usage_id,
                    "repair": False,
                }
            )
            try:
                if parse_error is not None:
                    raise parse_error
                validated = self._validate_description(authoritative, candidate)
            except VisualSemanticValidationError as first_error:
                if calls + 1 > max_calls or (calls + 1) * cost_per_call > max_cost:
                    raise VisualSemanticBlocked(
                        "quota_exceeded",
                        "视觉语义结构化修复将超过已配置配额。",
                        "由管理员调整配额，或修正模型输出配置后重试。",
                    ) from first_error
                repair_started = now_ms()
                try:
                    with self._model_semaphore:
                        repaired = self._model_call(
                            client=client,
                            model_session_id=model_session_id,
                            model=model,
                            authoritative=authoritative,
                            image_paths=image_paths,
                            repair_error=first_error.code,
                            prior_candidate=candidate,
                        )
                except Exception as exc:
                    calls += 1
                    try:
                        self._record_usage(
                            model=model,
                            fragment_id=fragment_id,
                            call_index=calls,
                            started_at=repair_started,
                            status="failed",
                            error_code=str(exc).split(":", 1)[0],
                        )
                    except Exception:
                        pass
                    raise
                calls += 1
                request_id, local_usage_id = self._record_usage(
                    model=model,
                    fragment_id=fragment_id,
                    call_index=calls,
                    started_at=repair_started,
                    status="ok",
                )
                model_call_audits.append(
                    {
                        "fragment_id": fragment_id,
                        "keyframe_ids": keyframe_ids,
                        "image_count": 4,
                        "request_id": request_id,
                        "local_usage_id": local_usage_id,
                        "repair": True,
                    }
                )
                repairs += 1
                try:
                    validated = self._validate_description(authoritative, repaired)
                except VisualSemanticValidationError as second_error:
                    raise VisualSemanticValidationError(
                        "visual_semantic_structured_repair_exhausted",
                        detail=(f"{first_error.code},{second_error.code}"),
                    ) from second_error
            self._cache_write(cache_key, validated)
            validated_items.append(validated)

        candidate_payload = {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "items": validated_items,
        }
        published, digest, result_path = publish_visual_semantic_contract(
            tool_root=paths.root,
            asset_id=str(self.asset["asset_id"]),
            source_version=str(self.asset["content_sha256"]),
            analysis_run_id=self.analysis_run_id,
            current_visual_structure_run_id=self.visual_structure_run_id,
            current_visual_structure_result_hash=(self.visual_structure_result_hash),
            candidate=candidate_payload,
            visual_prompt_version=self.visual_prompt_version,
            model_config_id=self.model_config_id,
        )
        quality = _read_object(paths.root / QUALITY_PATH)
        quality.update(
            {
                "cache_hit_count": cache_hits,
                "model_call_count": calls,
                "image_count": calls * 4,
                "structured_repair_count": repairs,
                "estimated_cost_micros": calls * cost_per_call,
            }
        )
        _write_json(paths.root / QUALITY_PATH, quality)
        prompt_dir = tool_dir / "Prompt"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        (prompt_dir / "System.txt").write_text(SYSTEM_PROMPT, encoding="utf-8")
        _write_json(
            prompt_dir / "PromptManifest.json",
            PromptManifest(
                tool_use_session_id=paths.tool_use_session_id,
                step_id=str(step.step_id),
                tool_id=TOOL_ID,
                prompts=[
                    {
                        "system_prompt_path": (prompt_dir / "System.txt").relative_to(paths.root).as_posix(),
                        "prompt_version": self.visual_prompt_version,
                        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
                    }
                ],
                references=[
                    {
                        "visual_structure_run_id": (self.visual_structure_run_id),
                        "visual_structure_result_hash": (self.visual_structure_result_hash),
                        "cloud_visual_data_transfer_authorized": (self.allow_cloud_visual_data_transfer),
                    }
                ],
                model_calls=model_call_audits,
            ).model_dump(),
        )
        return ToolResult(
            tool_id=TOOL_ID,
            tool_name=str(tool.get("name") or tool.get("tool_name") or ""),
            step_id=str(step.step_id),
            status="completed",
            outputs={
                "schema_version": str(published["schema_version"]),
                "result_hash": digest,
                "fragment_count": len(published["items"]),
                "cache_hit_count": cache_hits,
                "model_call_count": calls,
                "image_count": calls * 4,
                "structured_repair_count": repairs,
            },
            result_paths=[result_path, MANIFEST_PATH, QUALITY_PATH],
            metrics={
                "fragment_count": len(published["items"]),
                "cache_hit_count": cache_hits,
                "model_call_count": calls,
                "image_count": calls * 4,
            },
        )


class VisualSemanticService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.asset_repo = ctx.media_library_repo
        self.task_repo = ctx.media_library_task_repo
        self.run_repo = getattr(ctx, "media_analysis_run_repo", None)
        if self.run_repo is None and getattr(ctx, "engine", None) is not None:
            self.run_repo = AnalysisRunRepository(ctx.engine)

    def start(
        self,
        asset_id: str,
        *,
        force: bool = False,
        allow_cloud_visual_data_transfer: bool = False,
        visual_prompt_version: str = PROMPT_VERSION_DEFAULT,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        require_media_library_feature("analysis_runs")
        require_media_library_feature("visual_semantic")
        asset = self.asset_repo.get(asset_id)
        if asset is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "media_asset_not_found",
                    "user_message": "素材不存在或已删除。",
                },
            )
        task = self.task_repo.get_by_asset(asset_id)
        if task is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "open_cut_task_missing",
                    "user_message": "素材对应的 OpenCut Task 不存在。",
                },
            )
        structure = self.run_repo.current(asset_id, "visual_structure")
        if structure is None or str(structure.get("status") or "") != "ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "visual_structure_not_ready",
                    "user_message": "视觉语义需要先完成当前画面结构分析。",
                    "suggested_action": "先运行画面结构分析。",
                },
            )
        current = self.run_repo.current(asset_id, "visual_semantic")
        if current is not None and str(current.get("status") or "") == "ready" and not force:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "visual_analysis_exists",
                    "user_message": "当前画面语义分析已经完成。",
                },
            )
        if str(task.get("visual_semantic_status") or "") in {
            "queued",
            "running",
        }:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "visual_semantic_active",
                    "user_message": "画面语义分析已经在运行。",
                },
            )
        session_id = int(task.get("session_id") or 0)
        session = self.ctx.session_repo.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "media_session_missing",
                    "user_message": "素材 Session 不存在。",
                },
            )
        prompt_version = str(visual_prompt_version or "").strip() or PROMPT_VERSION_DEFAULT
        policy = surface_policy(self.ctx, SURFACE_MEDIA_LIBRARY_VISUAL_SEMANTIC)
        model_config_id = str(policy.get("version") or "").strip() or "visual_semantic_default_v1"
        timestamp = now_ms()
        progress = {
            "step": "prepare",
            "label": "正在准备视觉语义输入",
            "completed": 0,
            "total": TOTAL_STEPS,
            "started_at": timestamp,
            "updated_at": timestamp,
            "elapsed_ms": 0,
        }
        upstream_refs = {
            "visual_structure_run_id": str(structure["analysis_run_id"]),
            "visual_structure_result_hash": str(structure.get("result_hash") or ""),
        }
        business_run = self.run_repo.create_queued(
            asset_id=asset_id,
            scheme="visual_semantic",
            timestamp=timestamp,
            progress=progress,
            prompt_version=prompt_version,
            model_config_id=model_config_id,
            upstream_refs=upstream_refs,
        )
        analysis_run_id = str(business_run["analysis_run_id"])
        op_id = operation_id or (f"mlvo_{timestamp}_{uuid.uuid4().hex[:10]}")
        threading.Thread(
            target=self._run,
            kwargs={
                "asset": asset,
                "task": task,
                "session": session,
                "structure": structure,
                "analysis_run_id": analysis_run_id,
                "started_at": timestamp,
                "allow_cloud_visual_data_transfer": (allow_cloud_visual_data_transfer),
                "visual_prompt_version": prompt_version,
                "model_config_id": model_config_id,
            },
            name=f"open-cut-visual-semantic-{task['id']}",
            daemon=True,
        ).start()
        return {
            "status": "queued",
            "operation_id": op_id,
            "structure_run_id": str(structure["analysis_run_id"]),
            "semantic_run_id": analysis_run_id,
        }

    @staticmethod
    def _structure_paths(
        *,
        workspace: Path,
        structure: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        tool_use_session_id = str(structure.get("tool_use_session_id") or "")
        if not tool_use_session_id:
            raise ValueError("visual_structure_tool_session_missing")
        base = f"tool_use_sessions/{tool_use_session_id}"
        segments_rel = str(structure.get("result_index_path") or "")
        expected_prefix = f"{base}/"
        if not segments_rel.startswith(expected_prefix):
            raise ValueError("visual_structure_result_path_invalid")
        segments_path = (workspace / segments_rel).resolve()
        if not segments_path.is_relative_to(workspace.resolve()) or not segments_path.is_file():
            raise ValueError("visual_structure_result_missing")
        manifest_rel = f"{base}/SessionOutput/visual/visual_structure_manifest.json"
        manifest_path = (workspace / manifest_rel).resolve()
        if not manifest_path.is_relative_to(workspace.resolve()) or not manifest_path.is_file():
            raise ValueError("visual_structure_manifest_missing")
        segments = _read_object(segments_path)
        manifest = _read_object(manifest_path)
        expected_hash = str(structure.get("result_hash") or "")
        if result_hash(segments) != expected_hash:
            raise ValueError("visual_structure_result_hash_mismatch")
        if manifest.get("result_hash") != expected_hash:
            raise ValueError("visual_structure_manifest_hash_mismatch")
        return segments_rel, manifest_rel, segments, manifest

    def _run(
        self,
        *,
        asset: dict[str, Any],
        task: dict[str, Any],
        session: dict[str, Any],
        structure: dict[str, Any],
        analysis_run_id: str,
        started_at: int,
        allow_cloud_visual_data_transfer: bool,
        visual_prompt_version: str,
        model_config_id: str,
    ) -> None:
        workspace = Path(str(session["workspace_dir"])).resolve()
        session_id = int(task["session_id"])
        tool_use_session_id = ""
        runner: ToolSessionRunner | None = None
        run_finalized = False
        terminal_status = "failed"
        try:
            (
                segments_rel,
                manifest_rel,
                segments,
                _structure_manifest,
            ) = self._structure_paths(workspace=workspace, structure=structure)
            registered = self.ctx.session_repo.get_file(session_id, segments_rel)
            if registered is None:
                raise ValueError("visual_structure_result_not_registered")
            input_files = [
                PrepareInputFile(
                    source_path=manifest_rel,
                    target_name="visual_inputs/visual_structure_manifest.json",
                    visibility="internal",
                ),
                PrepareInputFile(
                    source_path=segments_rel,
                    target_name="visual_inputs/visual_structure_segments.json",
                    visibility="internal",
                ),
            ]
            frozen_keyframes: list[dict[str, str]] = []
            keyframe_sources: list[tuple[str, str]] = []
            structure_tool_session_id = str(structure["tool_use_session_id"])
            for item in segments.get("items") or []:
                if not isinstance(item, dict):
                    raise ValueError("visual_structure_item_invalid")
                keyframes = item.get("keyframes")
                if not isinstance(keyframes, list) or len(keyframes) != 4:
                    raise ValueError("visual_structure_four_keyframes_required")
                fragment_id = str(item.get("fragment_id") or "")
                for slot_index, keyframe in enumerate(keyframes):
                    if not isinstance(keyframe, dict):
                        raise ValueError("visual_structure_keyframe_invalid")
                    keyframe_id = str(keyframe.get("keyframe_id") or "")
                    expected_id = (
                        f"{fragment_id}-sample-{slot_index + 1:02d}"
                    )
                    if (
                        keyframe_id != expected_id
                        or not SAFE_KEYFRAME_ID_RE.fullmatch(keyframe_id)
                    ):
                        raise ValueError("visual_structure_keyframe_id_unsafe")
                    source_path = str(keyframe.get("image_path") or "")
                    suffix = Path(source_path).suffix.lower() or ".jpg"
                    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
                        raise ValueError(
                            "visual_structure_keyframe_suffix_invalid"
                        )
                    source_rel = (
                        f"tool_use_sessions/{structure_tool_session_id}/"
                        f"{source_path.lstrip('/')}"
                    )
                    if self.ctx.session_repo.get_file(
                        session_id, source_rel
                    ) is None:
                        raise ValueError(
                            "visual_structure_keyframe_not_registered:"
                            f"{keyframe_id}"
                        )
                    target_name = (
                        f"visual_inputs/keyframes/{keyframe_id}{suffix}"
                    )
                    input_files.append(
                        PrepareInputFile(
                            source_path=source_rel,
                            target_name=target_name,
                            visibility="internal",
                            sensitivity="normal",
                        )
                    )
                    frozen_keyframes.append(
                        {
                            "keyframe_id": keyframe_id,
                            "image_sha256": str(
                                keyframe.get("image_sha256") or ""
                            ),
                        }
                    )
                    keyframe_sources.append((source_rel, target_name))
            prepared = ToolSessionService(event_sink=self.ctx.session_event_service).start(
                PrepareSessionVariablesInput(
                    workspace_dir=workspace,
                    workflow_id=WORKFLOW_ID,
                    task_id=int(task["id"]),
                    opencrew_session_id=session_id,
                    selected_scheme="visual_semantic",
                    input_files=input_files,
                ),
                session_id=session_id,
            )
            tool_use_session_id = prepared.tool_use_session_id
            tool_root = workspace / "tool_use_sessions" / tool_use_session_id
            runner = ToolSessionRunner(
                workspace_dir=workspace,
                tool_use_session_id=tool_use_session_id,
                session_id=session_id,
                event_sink=self.ctx.session_event_service,
            )
            for source_rel, target_name in keyframe_sources:
                source_path = (workspace / source_rel).resolve()
                target_path = (tool_root / "0_SessionContext" / target_name).resolve()
                if not source_path.is_relative_to(workspace) or not target_path.is_relative_to(tool_root.resolve()):
                    raise ValueError("visual_semantic_snapshot_path_invalid")
                link_temp = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.link")
                try:
                    os.link(source_path, link_temp)
                    os.replace(link_temp, target_path)
                except OSError:
                    try:
                        link_temp.unlink(missing_ok=True)
                    except OSError:
                        pass
            _write_json(
                tool_root / INPUT_MANIFEST_REL,
                {
                    "schema_version": INPUT_SCHEMA_VERSION,
                    "asset_id": str(asset["asset_id"]),
                    "source_version": str(asset["content_sha256"]),
                    "visual_structure_run_id": str(structure["analysis_run_id"]),
                    "visual_structure_result_hash": str(structure["result_hash"]),
                    "sampling_strategy": SAMPLING_STRATEGY,
                    "keyframes": frozen_keyframes,
                    "visual_prompt_version": visual_prompt_version,
                    "model_config_id": model_config_id,
                    "allow_cloud_visual_data_transfer": (allow_cloud_visual_data_transfer),
                },
            )
            progress = {
                "step": TOOL_ID,
                "label": "正在分析受控 Keyframe",
                "completed": 1,
                "total": TOTAL_STEPS,
                "started_at": started_at,
                "updated_at": now_ms(),
                "elapsed_ms": max(0, now_ms() - started_at),
            }
            self.run_repo.mark_running(
                analysis_run_id,
                timestamp=now_ms(),
                tool_use_session_id=tool_use_session_id,
                progress=progress,
            )
            registry = normalize_registry_file(OPEN_CUT_REGISTRY, strict=True)
            adapter = VisualSemanticToolAdapter(
                ctx=self.ctx,
                session=session,
                asset={**asset, "task_id": task["id"]},
                analysis_run_id=analysis_run_id,
                visual_structure_run_id=str(structure["analysis_run_id"]),
                visual_structure_result_hash=str(structure["result_hash"]),
                visual_prompt_version=visual_prompt_version,
                model_config_id=model_config_id,
                allow_cloud_visual_data_transfer=(allow_cloud_visual_data_transfer),
            )
            run_result = runner.run_registry_step(
                step_id=STEP_ID,
                tool_id=TOOL_ID,
                normalized_registry=registry,
                step_index=1,
                adapters={TOOL_ID: adapter},
            )
            if run_result.status != "completed":
                terminal_status = "blocked" if run_result.status == "blocked" else "failed"
                error = run_result.outputs.get("error")
                if isinstance(error, dict):
                    raise VisualSemanticBlocked(
                        str(error.get("code") or "analysis_blocked"),
                        str(error.get("user_message") or "视觉语义分析被阻止。"),
                        str(error.get("suggested_action") or "检查模型配置后重试。"),
                    )
                raise RuntimeError("; ".join(run_result.errors) or "视觉语义工具执行失败。")
            sync_result = finalize_analysis_tool_session(self.ctx, runner, terminal_status="completed")
            run_finalized = True
            if sync_result.status != "completed":
                raise RuntimeError("视觉语义结果登记失败：" f"{result_sync_error(sync_result)}")
            published = _read_object(tool_root / RESULT_PATH)
            manifest = _read_object(tool_root / MANIFEST_PATH)
            finished_at = now_ms()
            completed_progress = {
                "step": "completed",
                "label": "画面语义分析已完成",
                "completed": TOTAL_STEPS,
                "total": TOTAL_STEPS,
                "started_at": started_at,
                "updated_at": finished_at,
                "finished_at": finished_at,
                "elapsed_ms": max(0, finished_at - started_at),
            }
            result_index_path = (
                f"tool_use_sessions/{tool_use_session_id}/{RESULT_PATH}"
            )
            visual_search = media_library_feature_state(
                "visual_search_v1"
            )
            if visual_search.configuration_valid and visual_search.enabled:
                fragments = [
                    {
                        "fragment_id": item["fragment_id"],
                        "start_ms": item["start_ms"],
                        "end_ms": item["end_ms"],
                        "dialogue_text": None,
                        "title": None,
                        "summary": item.get("visual_summary"),
                        "keywords": item.get("keywords") or [],
                        "visual_labels": [
                            *(item.get("people") or []),
                            *(item.get("objects") or []),
                            *(
                                [item["scene"]]
                                if item.get("scene")
                                else []
                            ),
                        ],
                        "keyframe_ref": item.get("keyframe_refs") or [],
                        "confidence": item.get("confidence"),
                        "needs_review": bool(item.get("needs_review")),
                    }
                    for item in (published.get("items") or [])
                ]
                self.ctx.media_library_fragment_publisher.publish_visual_semantic(
                    asset_id=str(asset["asset_id"]),
                    analysis_run_id=analysis_run_id,
                    result_hash=str(manifest["result_hash"]),
                    fragments=fragments,
                    timestamp=finished_at,
                    result_index_path=result_index_path,
                    visual_structure_run_id=str(
                        structure["analysis_run_id"]
                    ),
                    visual_structure_result_hash=str(
                        structure["result_hash"]
                    ),
                    progress=completed_progress,
                )
            else:
                activated = self.run_repo.activate_ready(
                    analysis_run_id,
                    timestamp=finished_at,
                    schema_version=str(published["schema_version"]),
                    result_hash=str(manifest["result_hash"]),
                    result_index_path=result_index_path,
                    progress=completed_progress,
                    upstream_refs={
                        "visual_structure_run_id": str(
                            structure["analysis_run_id"]
                        ),
                        "visual_structure_result_hash": str(
                            structure["result_hash"]
                        ),
                    },
                    expected_current_upstreams={
                        "visual_structure": {
                            "analysis_run_id": str(
                                structure["analysis_run_id"]
                            ),
                            "result_hash": str(structure["result_hash"]),
                        }
                    },
                )
                if str(activated.get("status") or "") == "stale":
                    self.ctx.session_event_service.add_event(
                        session_id,
                        "open_cut.visual_semantic.stale",
                        {
                            "asset_id": str(asset["asset_id"]),
                            "analysis_run_id": analysis_run_id,
                            "tool_use_session_id": tool_use_session_id,
                            "error": activated.get("error_json") or {},
                        },
                        workflow_id=WORKFLOW_ID,
                    )
                    return
            self.asset_repo.update_visual_analysis(
                str(asset["asset_id"]),
                status=None,
                updated_at=finished_at,
                fragment_count=len(published.get("items") or []),
            )
            self.ctx.session_event_service.add_event(
                session_id,
                "open_cut.visual_semantic.completed",
                {
                    "asset_id": str(asset["asset_id"]),
                    "analysis_run_id": analysis_run_id,
                    "tool_use_session_id": tool_use_session_id,
                    "result_hash": str(manifest["result_hash"]),
                    "fragment_count": len(published.get("items") or []),
                    "allow_cloud_visual_data_transfer": (allow_cloud_visual_data_transfer),
                },
                workflow_id=WORKFLOW_ID,
            )
        except Exception as exc:
            blocked = isinstance(exc, VisualSemanticBlocked)
            stale = (
                isinstance(exc, HTTPException)
                and isinstance(exc.detail, dict)
                and exc.detail.get("code") == "analysis_upstream_changed"
            )
            if blocked:
                terminal_status = "blocked"
            elif stale:
                terminal_status = "stale"
            sync_errors: list[str] = []
            if runner is not None and not run_finalized:
                try:
                    sync_result = finalize_analysis_tool_session(self.ctx, runner, terminal_status=terminal_status)
                    run_finalized = True
                    sync_errors = list(sync_result.errors)
                except Exception as sync_exc:
                    sync_errors = [str(sync_exc).strip() or sync_exc.__class__.__name__]
            finished_at = now_ms()
            if isinstance(exc, VisualSemanticBlocked):
                error = exc.payload()
            elif stale:
                error = dict(exc.detail)
            else:
                error = {
                    "code": "visual_semantic_execution_failed",
                    "user_message": str(exc).strip() or "视觉语义分析失败。",
                    "suggested_action": "检查输入与模型服务后重新运行。",
                }
            error.update(
                {
                    "run_id": analysis_run_id,
                    "failed_step": TOOL_ID,
                    "result_sync_errors": sync_errors,
                }
            )
            failed_progress = {
                "step": terminal_status,
                "label": (
                    "画面语义分析被阻止"
                    if terminal_status == "blocked"
                    else "画面语义上游已变化"
                    if terminal_status == "stale"
                    else "画面语义分析失败"
                ),
                "completed": 0,
                "total": TOTAL_STEPS,
                "started_at": started_at,
                "updated_at": finished_at,
                "finished_at": finished_at,
                "elapsed_ms": max(0, finished_at - started_at),
            }
            self.run_repo.finish_unsuccessful(
                analysis_run_id,
                status=terminal_status,
                timestamp=finished_at,
                error_code=str(error["code"]),
                error=error,
                progress=failed_progress,
            )
            self.ctx.session_event_service.add_event(
                session_id,
                f"open_cut.visual_semantic.{terminal_status}",
                {
                    "asset_id": str(asset["asset_id"]),
                    "analysis_run_id": analysis_run_id,
                    "tool_use_session_id": tool_use_session_id,
                    "error": {key: value for key, value in error.items() if key != "result_sync_errors"},
                    "result_sync_errors": sync_errors,
                },
                workflow_id=WORKFLOW_ID,
            )


def load_visual_semantic_result(
    *,
    workspace: Path,
    run: dict[str, Any],
) -> dict[str, Any]:
    relative = Path(str(run.get("result_index_path") or ""))
    if not relative.as_posix():
        return {"items": [], "error": "画面语义结果路径不存在。"}
    root = workspace.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return {"items": [], "error": "画面语义结果文件不存在。"}
    try:
        payload = _read_object(path)
    except Exception as exc:
        return {
            "items": [],
            "error": f"画面语义结果无法读取：{exc}",
        }
    return {
        "items": [item for item in (payload.get("items") or []) if isinstance(item, dict)],
        "error": None,
    }


__all__ = [
    "PROMPT_VERSION_DEFAULT",
    "VisualSemanticBlocked",
    "VisualSemanticService",
    "VisualSemanticToolAdapter",
    "load_visual_semantic_result",
]
