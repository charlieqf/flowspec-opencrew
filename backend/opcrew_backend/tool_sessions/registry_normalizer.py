from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RUNTIME_DEPENDENCIES = {
    "demucs",
    "easyocr",
    "ffmpeg",
    "paddleocr",
    "rapidocr",
    "tesseract",
}

PYTHON_PACKAGE_DEPENDENCIES = {
    "resemblyzer",
    "scenedetect",
    "speechbrain",
}

DATA_ASSET_DEPENDENCIES = {
    "voice_catalog": {
        "path": "ToolLibrary/Analysis_V1/VoiceCatalog",
        "description": "Curated Analysis_V1 Gemini voice catalog.",
    },
}

ENVIRONMENT_HINT_DEPENDENCIES = {
    "gpu_or_mps_for_speed",
}

SESSION_CONTEXT_DEPENDENCIES = {
    "opencode_image_model",
    "source_video",
    "source_video_or_audio",
    "task_id",
}

PROVIDER_CONFIG_DEPENDENCIES = {
    "OPENCREW_DATABASE_URL",
    "tool_asr_provider_configs",
    "tool_media_provider_configs",
}

OUTPUT_CONTRACT_DEPENDENCIES = {
    "storyboard_json": {
        "output_contract": "analysis_v1_storyboard",
        "path": "SessionOutput/storyboard/srt_storyboard.json",
        "producer_tool_ids": ["04_02", "04_03"],
    },
    "video_generation_plan_json": {
        "output_contract": "analysis_v1_video_generation_plan",
        "path": "SessionOutput/storyboard/video_generation_plan.json",
        "producer_tool_ids": ["05_01"],
    },
    "image_generation_plan_json": {
        "output_contract": "analysis_v1_image_generation_plan",
        "path": "SessionOutput/storyboard/image_generation_plan.json",
        "producer_tool_ids": ["05_03"],
    },
    "video_only_generation_plan_json": {
        "output_contract": "analysis_v1_video_only_generation_plan",
        "path": "SessionOutput/storyboard/video_only_generation_plan.json",
        "producer_tool_ids": ["05_05"],
    },
    # Soft "logic reuse" reference: 05_03/05_05 reuse the video-plan planning logic from 05_01.
    "video_generation_plan_logic": {
        "output_contract": "analysis_v1_video_generation_plan",
        "path": "SessionOutput/storyboard/video_generation_plan.json",
        "producer_tool_ids": ["05_01"],
    },
    # Soft reference: 04_01_free may reuse TTS reference speaking-speed from the TTS builders.
    "tts_reference_speed": {
        "output_contract": "analysis_v1_tts_reference_speed",
        "path": "SessionOutput/tts",
        "producer_tool_ids": ["03_02", "03_03"],
    },
}

RELATIVE_RUNTIME_SECONDS = {
    "very_low": 60,
    "low": 180,
    "medium": 600,
    "high": 1800,
    "very_high": 3600,
}


class RegistryNormalizationError(ValueError):
    def __init__(self, unresolved: list[dict[str, Any]]) -> None:
        self.unresolved = unresolved
        details = ", ".join(f"{item['tool_id']}:{item['token']}" for item in unresolved)
        super().__init__(f"Unresolved registry dependency tokens: {details}")


@dataclass(frozen=True)
class DependencyBuckets:
    consumes_outputs: list[dict[str, Any]]
    reads_session_context: list[str]
    runtime_dependencies: list[str]
    python_package_dependencies: list[str]
    data_asset_dependencies: list[dict[str, Any]]
    environment_hints: list[str]
    provider_config_dependencies: list[str]
    manual_dependencies: list[dict[str, Any]]
    unresolved: list[dict[str, Any]]


def load_registry(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("registry must be a JSON object")
    return value


def _tool_ids(tools: list[dict[str, Any]]) -> set[str]:
    return {str(tool.get("id") or "").strip() for tool in tools if str(tool.get("id") or "").strip()}


def _known_manual_dependency(token: str, overrides: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if token not in overrides:
        return None
    value = dict(overrides[token])
    value.setdefault("source_token", token)
    value.setdefault("manual_review_required", False)
    return value


def _normalize_dependencies(
    *,
    tool_id: str,
    tokens: list[str],
    all_tool_ids: set[str],
    manual_dependency_overrides: dict[str, dict[str, Any]],
) -> DependencyBuckets:
    consumes_outputs: list[dict[str, Any]] = []
    reads_session_context: list[str] = []
    runtime_dependencies: list[str] = []
    python_package_dependencies: list[str] = []
    data_asset_dependencies: list[dict[str, Any]] = []
    environment_hints: list[str] = []
    provider_config_dependencies: list[str] = []
    manual_dependencies: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for raw in tokens:
        token = str(raw or "").strip()
        if not token:
            continue
        manual = _known_manual_dependency(token, manual_dependency_overrides)
        if manual is not None:
            manual_dependencies.append(manual)
        elif token in OUTPUT_CONTRACT_DEPENDENCIES:
            output_dependency = dict(OUTPUT_CONTRACT_DEPENDENCIES[token])
            output_dependency["source_token"] = token
            consumes_outputs.append(output_dependency)
        elif token in all_tool_ids:
            consumes_outputs.append({"tool_id": token, "output_contract": "", "path": ""})
        elif token in SESSION_CONTEXT_DEPENDENCIES:
            reads_session_context.append(token)
        elif token in RUNTIME_DEPENDENCIES:
            runtime_dependencies.append(token)
        elif token in PYTHON_PACKAGE_DEPENDENCIES:
            python_package_dependencies.append(token)
        elif token in DATA_ASSET_DEPENDENCIES:
            data_asset = dict(DATA_ASSET_DEPENDENCIES[token])
            data_asset["source_token"] = token
            data_asset_dependencies.append(data_asset)
        elif token in ENVIRONMENT_HINT_DEPENDENCIES:
            environment_hints.append(token)
        elif token in PROVIDER_CONFIG_DEPENDENCIES:
            provider_config_dependencies.append(token)
        else:
            unresolved.append(
                {
                    "tool_id": tool_id,
                    "token": token,
                    "suggested_kind": "manual_dependencies",
                    "suggested_action": "Normalize this dependency as tool_output, session_context, plan_parameter, runtime_dependency, user_input, or manual_override.",
                }
            )
    return DependencyBuckets(
        consumes_outputs=consumes_outputs,
        reads_session_context=sorted(set(reads_session_context)),
        runtime_dependencies=sorted(set(runtime_dependencies)),
        python_package_dependencies=sorted(set(python_package_dependencies)),
        data_asset_dependencies=data_asset_dependencies,
        environment_hints=sorted(set(environment_hints)),
        provider_config_dependencies=sorted(set(provider_config_dependencies)),
        manual_dependencies=manual_dependencies,
        unresolved=unresolved,
    )


def _optional_dependency_entries(buckets: DependencyBuckets) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in buckets.consumes_outputs:
        entries.append({"kind": "tool_output", "required": False, **item})
    for item in buckets.reads_session_context:
        entries.append({"kind": "session_context", "required": False, "ref": item})
    for item in buckets.runtime_dependencies:
        entries.append({"kind": "runtime_dependency", "required": False, "ref": item})
    for item in buckets.python_package_dependencies:
        entries.append({"kind": "python_package", "required": False, "ref": item})
    for item in buckets.data_asset_dependencies:
        entries.append({"kind": "data_asset", "required": False, **item})
    for item in buckets.environment_hints:
        entries.append({"kind": "environment_hint", "required": False, "ref": item})
    for item in buckets.provider_config_dependencies:
        entries.append({"kind": "provider_config", "required": False, "ref": item})
    for item in buckets.manual_dependencies:
        value = dict(item)
        value.setdefault("kind", "manual_dependency")
        value["required"] = False
        entries.append(value)
    return entries


def estimated_runtime_seconds(tool: dict[str, Any]) -> int:
    estimated = tool.get("estimated_runtime")
    if isinstance(estimated, dict):
        relative = str(estimated.get("relative") or "").strip()
        if relative in RELATIVE_RUNTIME_SECONDS:
            return RELATIVE_RUNTIME_SECONDS[relative]
    value = tool.get("estimated_runtime_seconds")
    if isinstance(value, int) and value > 0:
        return value
    return 300


def normalize_registry(
    registry: dict[str, Any],
    *,
    manual_dependency_overrides: dict[str, dict[str, Any]] | None = None,
    strict: bool = True,
    tool_ids: set[str] | None = None,
) -> dict[str, Any]:
    overrides = manual_dependency_overrides or {}
    tools = list(registry.get("tools") or [])
    if tool_ids is not None:
        tools = [tool for tool in tools if str(tool.get("id") or "") in tool_ids]
    all_tool_ids = _tool_ids(list(registry.get("tools") or []))
    normalized_tools: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for tool in tools:
        tool_id = str(tool.get("id") or "").strip()
        hard_tokens = [str(item) for item in tool.get("hard_dependencies") or []]
        soft_tokens = [str(item) for item in tool.get("soft_dependencies") or []]
        hard = _normalize_dependencies(
            tool_id=tool_id,
            tokens=hard_tokens,
            all_tool_ids=all_tool_ids,
            manual_dependency_overrides=overrides,
        )
        soft = _normalize_dependencies(
            tool_id=tool_id,
            tokens=soft_tokens,
            all_tool_ids=all_tool_ids,
            manual_dependency_overrides=overrides,
        )
        tool_unresolved = hard.unresolved + soft.unresolved
        unresolved.extend(tool_unresolved)

        seconds = estimated_runtime_seconds(tool)
        uses_llm = bool(tool.get("uses_llm", False))
        uses_vlm = bool(tool.get("uses_vlm", False))
        uses_tts = bool(tool.get("uses_tts", False))
        uses_image = bool(tool.get("uses_image", False))
        uses_video = bool(tool.get("uses_video", False))
        uses_audio = bool(tool.get("uses_audio", False))
        uses_model = uses_llm or uses_vlm or uses_tts or uses_image or uses_video or uses_audio
        normalized_tools.append(
            {
                "id": tool_id,
                "tool_id": tool_id,
                "name": tool.get("name") or "",
                "tool_name": tool.get("name") or "",
                "script": tool.get("script") or "",
                "stage": tool.get("stage") or "",
                "required_by_default": bool(tool.get("required_by_default", False)),
                "cost_level": tool.get("cost_level") or "medium",
                "requires_confirmation": str(tool.get("cost_level") or "") in {"high", "very_high"},
                "uses_llm": uses_llm,
                "uses_vlm": uses_vlm,
                "uses_tts": uses_tts,
                "uses_image": uses_image,
                "uses_video": uses_video,
                "uses_audio": uses_audio,
                "model_requirements": {
                    "uses_llm": uses_llm,
                    "uses_vlm": uses_vlm,
                    "uses_tts": uses_tts,
                    "uses_image": uses_image,
                    "uses_video": uses_video,
                    "uses_audio": uses_audio,
                    "call_path": "broker_resolver" if uses_model else "",
                },
                "supports_resume": bool(tool.get("supports_resume", False)),
                "supports_progress_log": bool(tool.get("supports_progress_log", False)),
                "writes_session_context": list(tool.get("writes_session_context") or tool.get("variables_ownership") or []),
                "variables_ownership": list(tool.get("variables_ownership") or tool.get("writes_session_context") or []),
                "reads_session_context": hard.reads_session_context,
                "consumes_outputs": hard.consumes_outputs,
                "runtime_dependencies": hard.runtime_dependencies,
                "python_package_dependencies": hard.python_package_dependencies,
                "data_asset_dependencies": hard.data_asset_dependencies,
                "environment_hints": hard.environment_hints,
                "provider_config_dependencies": hard.provider_config_dependencies,
                "manual_dependencies": hard.manual_dependencies,
                "optional_dependencies": _optional_dependency_entries(soft),
                "produces_outputs": list(tool.get("main_outputs") or []),
                "estimated_runtime": tool.get("estimated_runtime") or {},
                "estimated_runtime_seconds": seconds,
                "heartbeat_timeout_seconds": int(tool.get("heartbeat_timeout_seconds") or max(seconds * 3, 300)),
                "unresolved_dependencies": tool_unresolved,
            }
        )

    if strict and unresolved:
        raise RegistryNormalizationError(unresolved)

    return {
        "schema_version": "1.0",
        "source_schema_version": str(registry.get("schema_version") or ""),
        "description": "Normalized Tool Library registry for Tool Use Session runner.",
        "tools": normalized_tools,
        "unresolved_dependencies": unresolved,
    }


def normalize_registry_file(
    path: Path | str,
    *,
    manual_dependency_overrides: dict[str, dict[str, Any]] | None = None,
    strict: bool = True,
    tool_ids: set[str] | None = None,
) -> dict[str, Any]:
    return normalize_registry(
        load_registry(path),
        manual_dependency_overrides=manual_dependency_overrides,
        strict=strict,
        tool_ids=tool_ids,
    )


def write_normalized_registry_file(
    source_path: Path | str,
    output_path: Path | str,
    *,
    manual_dependency_overrides: dict[str, dict[str, Any]] | None = None,
    strict: bool = True,
    tool_ids: set[str] | None = None,
) -> dict[str, Any]:
    normalized = normalize_registry_file(
        source_path,
        manual_dependency_overrides=manual_dependency_overrides,
        strict=strict,
        tool_ids=tool_ids,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    return normalized
