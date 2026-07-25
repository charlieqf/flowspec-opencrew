from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .paths import create_tool_use_session_id
from .prepare import PrepareSessionVariablesInput, prepare_session_variables


logger = logging.getLogger(__name__)


class ToolSessionEventSink(Protocol):
    def add_event(
        self,
        session_id: int,
        kind: str,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> int:
        ...


@dataclass(frozen=True)
class ToolSessionStartResult:
    tool_use_session_id: str
    root: Path
    variables_path: Path
    input_manifest_path: Path
    s0_state_path: Path


class ToolSessionService:
    def __init__(self, *, event_sink: ToolSessionEventSink | None = None) -> None:
        self.event_sink = event_sink

    def start(
        self,
        params: PrepareSessionVariablesInput,
        *,
        session_id: int | None = None,
    ) -> ToolSessionStartResult:
        tool_use_session_id = params.tool_use_session_id or create_tool_use_session_id()
        prepared_params = replace(params, tool_use_session_id=tool_use_session_id)
        self._emit(
            session_id,
            "tool_session.prepare.started",
            {
                "tool_use_session_id": tool_use_session_id,
                "workspace_dir": str(params.workspace_dir),
            },
            workflow_id=params.workflow_id or None,
            task_id=params.task_id,
            attempt_id=params.attempt_id,
            step_id="S0",
            tool_id="00",
        )
        try:
            paths = prepare_session_variables(prepared_params)
        except Exception as exc:
            self._emit(
                session_id,
                "tool_session.prepare.failed",
                {
                    "tool_use_session_id": tool_use_session_id,
                    "error": str(exc),
                },
                workflow_id=params.workflow_id or None,
                task_id=params.task_id,
                attempt_id=params.attempt_id,
                step_id="S0",
                tool_id="00",
            )
            raise

        self._emit(
            session_id,
            "tool_session.prepare.completed",
            {
                "tool_use_session_id": tool_use_session_id,
                "root": str(paths["root"]),
                "variables": str(paths["variables"]),
                "input_manifest": str(paths["input_manifest"]),
            },
            workflow_id=params.workflow_id or None,
            task_id=params.task_id,
            attempt_id=params.attempt_id,
            step_id="S0",
            tool_id="00",
        )
        return ToolSessionStartResult(
            tool_use_session_id=tool_use_session_id,
            root=paths["root"],
            variables_path=paths["variables"],
            input_manifest_path=paths["input_manifest"],
            s0_state_path=paths["s0_state"],
        )

    def _emit(
        self,
        session_id: int | None,
        kind: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        if self.event_sink is None:
            return
        if session_id is None:
            logger.warning("Dropping tool session event without session_id", extra={"event_kind": kind, "payload": payload})
            return
        self.event_sink.add_event(session_id, kind, payload, **kwargs)
