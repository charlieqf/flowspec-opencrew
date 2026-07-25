from __future__ import annotations

from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]


WORKFLOW_CONFIGS = {
    "openclip_analysis": {
        "id": "openclip_analysis",
        "name": "OC-Analysis",
        "source": "openclip-analysis",
        "task_adapter": "openclip",
        "tool_library": {
            "root": "ToolLibrary/Analysis",
            "registry": "ToolLibrary/Analysis/tool_registry.json",
            "agent_guide": "ToolLibrary/Analysis/AGENT_TOOL_GUIDE.md",
        },
        "assistant": {
            "system_prompt_template": "openclip_assistant_system_prompt",
            "quick_prompts": "openclip_quick_prompts",
        },
        "context_builder": "openclip_context_builder",
        "plan_schema": "workflow_tool_plan_v1",
        "runner": "noop_runner",
    },
    "oc_rebuild": {
        "id": "oc_rebuild",
        "name": "OC-Rebuild",
        "source": "oc-rebuild",
        "task_adapter": "oc_rebuild",
        "tool_library": {
            "root": "ToolLibrary/Rebuild_V1",
            "registry": "ToolLibrary/Rebuild_V1/tool_registry.json",
            "agent_guide": "ToolLibrary/Rebuild_V1/README.md",
        },
        "assistant": {
            "system_prompt_template": "oc_rebuild_assistant_system_prompt",
            "quick_prompts": "oc_rebuild_quick_prompts",
        },
        "context_builder": "oc_rebuild_context_builder",
        "plan_schema": "workflow_tool_plan_v1",
        "runner": "noop_runner",
    },
    "oc_rebuild_plan_a_phase_batch_v0": {
        "id": "oc_rebuild_plan_a_phase_batch_v0",
        "name": "OC-Rebuild Plan A Phase-Batch Auto v0",
        "source": "oc-rebuild",
        "task_adapter": "oc_rebuild",
        "tool_library": {
            "root": "ToolLibrary/Rebuild_V1",
            "registry": "ToolLibrary/Rebuild_V1/tool_registry.json",
            "agent_guide": "ToolLibrary/Rebuild_V1/README.md",
        },
        "assistant": {
            "system_prompt_template": "oc_rebuild_plan_a_phase_batch_system_prompt",
            "quick_prompts": "oc_rebuild_plan_a_phase_batch_quick_prompts",
        },
        "context_builder": "oc_rebuild_context_builder",
        "plan_schema": "workflow_tool_plan_v1",
        "runner": "noop_runner",
    },
    "oc_rebuild_plan_a_shot_first_v1": {
        "id": "oc_rebuild_plan_a_shot_first_v1",
        "name": "OC-Rebuild Plan A Shot-First Auto v1",
        "source": "oc-rebuild",
        "task_adapter": "oc_rebuild",
        "tool_library": {
            "root": "ToolLibrary/Rebuild_V1",
            "registry": "ToolLibrary/Rebuild_V1/tool_registry.json",
            "agent_guide": "ToolLibrary/Rebuild_V1/README.md",
        },
        "assistant": {
            "system_prompt_template": "oc_rebuild_plan_a_shot_first_system_prompt",
            "quick_prompts": "oc_rebuild_plan_a_shot_first_quick_prompts",
        },
        "context_builder": "oc_rebuild_context_builder",
        "plan_schema": "workflow_tool_plan_v1",
        "runner": "noop_runner",
    },
}


def resolve_repo_path(configured_path: str | None) -> Path | None:
    if not configured_path:
        return None
    path = Path(str(configured_path))
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == REPO_ROOT.name:
        return REPO_ROOT.parent / path
    return REPO_ROOT / path


def validate_workflow_tool_library(workflow_id: str, config: dict[str, Any]) -> dict[str, Path]:
    tool_config = config.get("tool_library") or {}
    root = resolve_repo_path(tool_config.get("root"))
    registry = resolve_repo_path(tool_config.get("registry"))
    agent_guide = resolve_repo_path(tool_config.get("agent_guide"))
    errors: list[str] = []
    if root is None:
        errors.append("tool_library.root is not configured")
    elif not root.exists() or not root.is_dir():
        errors.append(f"tool_library.root does not exist: {root}")
    if registry is None:
        errors.append("tool_library.registry is not configured")
    elif not registry.exists() or not registry.is_file():
        errors.append(f"tool_library.registry does not exist: {registry}")
    if agent_guide is None:
        errors.append("tool_library.agent_guide is not configured")
    elif not agent_guide.exists() or not agent_guide.is_file():
        errors.append(f"tool_library.agent_guide does not exist: {agent_guide}")
    if root and registry and registry.exists():
        try:
            registry.resolve().relative_to(root.resolve())
        except ValueError:
            errors.append(f"tool_library.registry is outside root: {registry}")
    if root and agent_guide and agent_guide.exists():
        try:
            agent_guide.resolve().relative_to(root.resolve())
        except ValueError:
            errors.append(f"tool_library.agent_guide is outside root: {agent_guide}")
    if errors:
        joined = "; ".join(errors)
        raise RuntimeError(f"Invalid Tool Library config for workflow {workflow_id}: {joined}")
    return {"root": root, "registry": registry, "agent_guide": agent_guide}  # type: ignore[dict-item]


def resolve_workflow_tool_library(workflow_id: str) -> dict[str, Path]:
    config = WORKFLOW_CONFIGS.get(workflow_id)
    if not config:
        raise RuntimeError(f"Unknown workflow: {workflow_id}")
    return validate_workflow_tool_library(workflow_id, config)


def workflow_tool_library_source(workflow_id: str) -> dict[str, str]:
    paths = resolve_workflow_tool_library(workflow_id)
    return {
        "workflow_id": workflow_id,
        "root": paths["root"].as_posix(),
        "registry": paths["registry"].as_posix(),
        "agent_guide": paths["agent_guide"].as_posix(),
    }
