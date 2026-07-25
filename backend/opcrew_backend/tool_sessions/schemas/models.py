from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "1.0"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    schema_version: Literal["1.0"] = SCHEMA_VERSION


class Variables(ContractModel):
    tool_use_session_id: str
    workflow_id: str = ""
    task_id: int | None = None
    opencrew_session_id: int | None = None
    opencode_session_id: str = ""
    workspace_dir: str
    tool_session_root: str
    current_attempt_id: int | None = None
    attempt_no: int | None = None
    workflow_plan_id: int | None = None
    workflow_plan_run_id: int | None = None
    current_prompt_version_id: int | None = None
    current_runtime_version_id: int | None = None
    prompt_model_provider: str = ""
    prompt_model_id: str = ""
    run_model_provider: str = ""
    run_model_id: str = ""
    provider_mode: str = "local_box"
    billing_mode: str = "local_usage_only"
    source_video_path: str = ""
    clip_mode: str = ""
    selected_scheme: str = ""
    cloud_asr_data_transfer_allowed: bool = False
    cloud_asr_data_transfer_scope: str = ""
    cloud_asr_data_transfer_authorized_at: str = ""
    created_at: str = ""
    updated_at: str = ""


class InputFileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    source_kind: str = "workspace_file"
    source_ref: str = ""
    sha256: str
    size: int
    visibility: str = "internal"
    sensitivity: str = "normal"


class InputManifest(ContractModel):
    tool_use_session_id: str
    attempt_id: int | None = None
    files: list[InputFileEntry] = Field(default_factory=list)


class OutputFileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str = "artifact"
    schema_name: str = ""
    sha256: str = ""
    size: int = 0
    visibility: str = "internal"
    sensitivity: str = "normal"
    downloadable: int = 0


class OutputManifest(ContractModel):
    tool_use_session_id: str
    step_id: str
    tool_id: str
    status: str = "completed"
    files: list[OutputFileEntry] = Field(default_factory=list)


class State(ContractModel):
    tool_use_session_id: str
    attempt_id: int | None = None
    workflow_plan_run_id: int | None = None
    step_index: int = 0
    step_id: str
    tool_id: str
    tool_name: str
    status: str = "not_started"
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    heartbeat_at: str = ""
    retry_count: int = 0
    idempotency_key: str = ""
    input_snapshot_hash: str = ""
    output_manifest_path: str = "Output/OutputManifest.json"
    error_summary: dict[str, Any] | None = None


class MissingDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    required_from: str = ""
    required_path: str = ""
    suggested_action: str


class DependencyCheckResult(ContractModel):
    status: Literal["ready", "blocked"]
    missing_dependencies: list[MissingDependency] = Field(default_factory=list)

    @model_validator(mode="after")
    def blocked_requires_missing_dependencies(self) -> "DependencyCheckResult":
        if self.status == "blocked" and not self.missing_dependencies:
            raise ValueError("blocked dependency checks must include missing_dependencies")
        if self.status == "ready" and self.missing_dependencies:
            raise ValueError("ready dependency checks must not include missing_dependencies")
        return self


class PromptManifest(ContractModel):
    tool_use_session_id: str
    step_id: str
    tool_id: str
    prompts: list[dict[str, Any]] = Field(default_factory=list)
    references: list[dict[str, Any]] = Field(default_factory=list)
    model_calls: list[dict[str, Any]] = Field(default_factory=list)


class ModelCallAudit(ContractModel):
    tool_use_session_id: str
    step_index: int
    step_id: str = ""
    tool_name: str
    task_id: int | None = None
    opencrew_session_id: int | None = None
    opencode_session_id: str = ""
    attempt_id: int | None = None
    model_provider: str
    model_id: str
    system_prompt_path: str = ""
    user_prompt_path: str = ""
    input_files: list[str] = Field(default_factory=list)
    output_summary: str = ""
    error_summary: str = ""
    request_id: str
    local_usage_id: str = ""
    gateway_request_id: str = ""
    provider_mode: str = "local_box"
    billing_mode: str = "local_usage_only"
    usage_summary: dict[str, Any] = Field(default_factory=dict)
    sensitivity: str = "normal"
    redaction_status: str = "redacted"
    idempotency_key: str


class ToolResult(ContractModel):
    tool_id: str
    tool_name: str = ""
    step_id: str
    status: str
    outputs: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    result_paths: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    context_patch: dict[str, Any] = Field(default_factory=dict)


class SessionContextPatch(ContractModel):
    tool_id: str
    step_id: str
    patch: dict[str, Any] = Field(default_factory=dict)


class SessionRunSummary(ContractModel):
    tool_use_session_id: str
    attempt_id: int | None = None
    status: str = "running"
    steps: list[dict[str, Any]] = Field(default_factory=list)


class ResultIndex(ContractModel):
    attempt_id: int | None = None
    tool_use_session_id: str
    source_video: str = ""
    clip_mode: str = ""
    schemes: dict[str, Any] = Field(default_factory=dict)
    reports: dict[str, Any] = Field(default_factory=dict)
    tool_outputs: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class ResyncResult(ContractModel):
    tool_use_session_id: str
    attempt_id: int | None = None
    status: str
    synced_files: list[str] = Field(default_factory=list)
    synced_events: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
