from __future__ import annotations

from .._paths import ensure_repo_root_path

ensure_repo_root_path()
from WorkflowAssistant.backend.workflow_assistant import build_workflow_assistant_router

__all__ = ["build_workflow_assistant_router"]
