from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .io import copy_workspace_file
from .paths import ToolSessionPaths, create_tool_use_session_id, ensure_tool_session_layout
from .schemas import InputFileEntry, InputManifest, OutputManifest, State, Variables


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PrepareInputFile:
    source_path: str
    target_name: str
    source_kind: str = "workspace_file"
    visibility: str = "internal"
    sensitivity: str = "normal"


@dataclass(frozen=True)
class PrepareSessionVariablesInput:
    workspace_dir: Path
    workflow_id: str = ""
    task_id: int | None = None
    opencrew_session_id: int | None = None
    opencode_session_id: str = ""
    attempt_id: int | None = None
    attempt_no: int | None = None
    workflow_plan_id: int | None = None
    workflow_plan_run_id: int | None = None
    prompt_model_provider: str = ""
    prompt_model_id: str = ""
    run_model_provider: str = ""
    run_model_id: str = ""
    clip_mode: str = ""
    selected_scheme: str = ""
    allow_cloud_asr_data_transfer: bool = False
    tool_use_session_id: str = ""
    input_files: list[PrepareInputFile] = field(default_factory=list)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def _ensure_tool_dirs(tool_dir: Path) -> None:
    for part in ("Working", "Output", "Report", "Prompt"):
        (tool_dir / part).mkdir(parents=True, exist_ok=True)


def prepare_session_variables(params: PrepareSessionVariablesInput) -> dict[str, Path]:
    workspace_dir = params.workspace_dir.resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    tool_use_session_id = params.tool_use_session_id or create_tool_use_session_id()
    paths = ToolSessionPaths(workspace_dir=workspace_dir, tool_use_session_id=tool_use_session_id)
    ensure_tool_session_layout(paths)

    now = utc_now_iso()
    input_entries: list[InputFileEntry] = []
    source_video_path = ""
    for item in params.input_files:
        target_rel = f"0_SessionContext/{item.target_name}"
        target_path = paths.context_dir / item.target_name
        checksum, size = copy_workspace_file(workspace_dir, item.source_path, target_path)
        if not source_video_path and item.target_name.lower().startswith("video_"):
            source_video_path = target_rel
        input_entries.append(
            InputFileEntry(
                path=target_rel,
                source_kind=item.source_kind,
                source_ref=item.source_path,
                sha256=checksum,
                size=size,
                visibility=item.visibility,
                sensitivity=item.sensitivity,
            )
        )

    variables = Variables(
        tool_use_session_id=tool_use_session_id,
        workflow_id=params.workflow_id,
        task_id=params.task_id,
        opencrew_session_id=params.opencrew_session_id,
        opencode_session_id=params.opencode_session_id,
        workspace_dir=str(workspace_dir),
        tool_session_root=f"tool_use_sessions/{tool_use_session_id}",
        current_attempt_id=params.attempt_id,
        attempt_no=params.attempt_no,
        workflow_plan_id=params.workflow_plan_id,
        workflow_plan_run_id=params.workflow_plan_run_id,
        prompt_model_provider=params.prompt_model_provider,
        prompt_model_id=params.prompt_model_id,
        run_model_provider=params.run_model_provider,
        run_model_id=params.run_model_id,
        source_video_path=source_video_path,
        clip_mode=params.clip_mode,
        selected_scheme=params.selected_scheme,
        cloud_asr_data_transfer_allowed=bool(params.allow_cloud_asr_data_transfer),
        cloud_asr_data_transfer_scope="task_audio_to_configured_asr_provider" if params.allow_cloud_asr_data_transfer else "",
        cloud_asr_data_transfer_authorized_at=now if params.allow_cloud_asr_data_transfer else "",
        created_at=now,
        updated_at=now,
    )
    manifest = InputManifest(tool_use_session_id=tool_use_session_id, attempt_id=params.attempt_id, files=input_entries)

    _write_json(paths.context_dir / "Variables.json", variables.model_dump())
    _write_json(paths.context_dir / "InputManifest.json", manifest.model_dump())

    s0_dir = paths.tool_dir(0, "00_PrepareSessionVariables")
    _ensure_tool_dirs(s0_dir)
    state = State(
        tool_use_session_id=tool_use_session_id,
        attempt_id=params.attempt_id,
        workflow_plan_run_id=params.workflow_plan_run_id,
        step_index=0,
        step_id="S0",
        tool_id="00",
        tool_name="00_PrepareSessionVariables",
        status="completed",
        started_at=now,
        updated_at=now,
        finished_at=now,
        heartbeat_at=now,
        idempotency_key=f"{tool_use_session_id}:S0:0",
    )
    output_manifest = OutputManifest(
        tool_use_session_id=tool_use_session_id,
        step_id="S0",
        tool_id="00",
        status="completed",
        files=[],
    )
    _write_json(s0_dir / "State.json", state.model_dump())
    _write_json(s0_dir / "Output" / "OutputManifest.json", output_manifest.model_dump())

    return {
        "root": paths.root,
        "context_dir": paths.context_dir,
        "variables": paths.context_dir / "Variables.json",
        "input_manifest": paths.context_dir / "InputManifest.json",
        "s0_state": s0_dir / "State.json",
    }
