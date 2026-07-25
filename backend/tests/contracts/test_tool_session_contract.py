from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from opcrew_backend.tool_sessions.io import WorkspacePathError  # noqa: E402
from opcrew_backend.tool_sessions.model_broker import ModelBroker, ModelBrokerRequest  # noqa: E402
from opcrew_backend.tool_sessions.prepare import (  # noqa: E402
    PrepareInputFile,
    PrepareSessionVariablesInput,
    prepare_session_variables,
)
from opcrew_backend.tool_sessions.registry_normalizer import (  # noqa: E402
    RegistryNormalizationError,
    normalize_registry_file,
    write_normalized_registry_file,
)
from opcrew_backend.tool_sessions.result_sync import ToolSessionResultSync  # noqa: E402
from opcrew_backend.tool_sessions.runner import RunnerStep, ToolSessionRunner  # noqa: E402
from opcrew_backend.tool_sessions.runner import SubprocessToolAdapter  # noqa: E402
from opcrew_backend.tool_sessions.runner import _scrub_subprocess_env, _select_subprocess_python  # noqa: E402
from opcrew_backend.tool_sessions.schemas import (  # noqa: E402
    DependencyCheckResult,
    MissingDependency,
    OutputFileEntry,
    OutputManifest,
    ResyncResult,
    ResultIndex,
    SessionContextPatch,
    ToolResult,
)
from opcrew_backend.tool_sessions.service import ToolSessionService  # noqa: E402


REGISTRY_PATH = REPO_ROOT / "ToolLibrary" / "Analysis" / "tool_registry.json"
FREE_TEXT_DEP_OVERRIDES = {
    "13 detail scheme": {"kind": "manual_override", "normalized_ref": "scheme_detail_13"},
    "14 detail segment descriptions": {"kind": "manual_override", "normalized_ref": "segment_descriptions_14"},
    "target balanced_or_summary": {"kind": "plan_parameter", "normalized_ref": "target_detail_level"},
    "task_opencode_session_or_fallback": {"kind": "session_context", "normalized_ref": "opencode_session_id"},
    "human_scene_transition_overrides": {"kind": "user_input", "normalized_ref": "scene_transition_overrides"},
    "user_recomposition_instruction": {"kind": "user_input", "normalized_ref": "recomposition_instruction"},
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ToolSessionSchemaContractTest(unittest.TestCase):
    def test_all_machine_readable_contracts_use_string_schema_version(self) -> None:
        result = DependencyCheckResult(status="ready")

        self.assertEqual(result.schema_version, "1.0")
        self.assertIsInstance(result.schema_version, str)

    def test_blocked_dependency_result_requires_standard_missing_dependency_payload(self) -> None:
        blocked = DependencyCheckResult(
            status="blocked",
            missing_dependencies=[
                MissingDependency(
                    kind="tool_output",
                    required_from="01",
                    required_path="meta/video_metadata.json",
                    suggested_action="Run tool 01 before this step.",
                )
            ],
        )

        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(blocked.missing_dependencies[0].required_from, "01")
        with self.assertRaises(ValidationError):
            DependencyCheckResult(status="blocked")
        with self.assertRaises(ValidationError):
            DependencyCheckResult(
                status="ready",
                missing_dependencies=[
                    MissingDependency(kind="tool_output", required_from="01", suggested_action="Run tool 01.")
                ],
            )

    def test_result_index_schema_is_publicly_exported(self) -> None:
        result_index = ResultIndex(tool_use_session_id="tus_contract")

        self.assertEqual(result_index.schema_version, "1.0")
        self.assertEqual(result_index.tool_use_session_id, "tus_contract")


class RegistryNormalizerContractTest(unittest.TestCase):
    def test_normalizes_existing_registry_fields_for_single_tool(self) -> None:
        normalized = normalize_registry_file(REGISTRY_PATH, strict=True, tool_ids={"01"})
        tool = normalized["tools"][0]

        self.assertEqual(normalized["schema_version"], "1.0")
        self.assertEqual(tool["id"], "01")
        self.assertEqual(tool["reads_session_context"], ["source_video"])
        self.assertEqual(tool["produces_outputs"], ["meta/video_metadata.json", "meta/video_metadata.md", "meta/run_result.json"])
        self.assertEqual(tool["estimated_runtime"]["relative"], "very_low")
        self.assertEqual(tool["estimated_runtime_seconds"], 60)
        self.assertEqual(tool["heartbeat_timeout_seconds"], 300)

    def test_soft_dependencies_are_preserved_as_optional_dependencies(self) -> None:
        normalized = normalize_registry_file(REGISTRY_PATH, strict=True, tool_ids={"02"})
        tool = normalized["tools"][0]

        self.assertEqual(tool["reads_session_context"], ["source_video"])
        self.assertEqual(tool["provider_config_dependencies"], [])
        optional = {(item["kind"], item["ref"]) for item in tool["optional_dependencies"]}
        self.assertEqual(
            optional,
            {
                ("provider_config", "OPENCREW_DATABASE_URL"),
                ("provider_config", "tool_asr_provider_configs"),
            },
        )

    def test_environment_hints_are_not_runtime_binaries(self) -> None:
        normalized = normalize_registry_file(REGISTRY_PATH, strict=True, tool_ids={"02_0"})
        tool = normalized["tools"][0]

        self.assertEqual(tool["runtime_dependencies"], ["demucs", "ffmpeg"])
        self.assertEqual(tool["environment_hints"], [])
        self.assertEqual(tool["optional_dependencies"], [{"kind": "environment_hint", "required": False, "ref": "gpu_or_mps_for_speed"}])

    def test_strict_normalization_fails_on_free_text_dependency_tokens(self) -> None:
        with self.assertRaises(RegistryNormalizationError) as ctx:
            normalize_registry_file(REGISTRY_PATH, strict=True)

        unresolved_tokens = {item["token"] for item in ctx.exception.unresolved}
        for token in FREE_TEXT_DEP_OVERRIDES:
            self.assertIn(token, unresolved_tokens)

    def test_manual_dependency_overrides_close_free_text_crosswalk(self) -> None:
        normalized = normalize_registry_file(
            REGISTRY_PATH,
            strict=True,
            manual_dependency_overrides=FREE_TEXT_DEP_OVERRIDES,
        )

        self.assertEqual(normalized["unresolved_dependencies"], [])
        tool_06 = next(tool for tool in normalized["tools"] if tool["id"] == "06")
        self.assertEqual(tool_06["estimated_runtime"]["relative"], "very_high")
        self.assertEqual(tool_06["estimated_runtime_seconds"], 3600)
        self.assertEqual(tool_06["heartbeat_timeout_seconds"], 10800)
        self.assertEqual(tool_06["model_requirements"]["call_path"], "broker_resolver")

    def test_normalized_registry_can_be_written_as_runner_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "normalized_registry.json"
            write_normalized_registry_file(REGISTRY_PATH, output_path, strict=True, tool_ids={"01"})

            artifact = read_json(output_path)

        self.assertEqual(artifact["schema_version"], "1.0")
        self.assertEqual(artifact["tools"][0]["id"], "01")


class PrepareSessionContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_prepare_creates_session_layout_and_s0_manifests(self) -> None:
        (self.workspace / "input").mkdir()
        (self.workspace / "input" / "source.mp4").write_bytes(b"fake-video")

        paths = prepare_session_variables(
            PrepareSessionVariablesInput(
                workspace_dir=self.workspace,
                workflow_id="openclip_analysis",
                attempt_id=42,
                tool_use_session_id="tus_contract",
                allow_cloud_asr_data_transfer=True,
                input_files=[
                    PrepareInputFile(
                        source_path="input/source.mp4",
                        target_name="Video_Source.mp4",
                        visibility="internal",
                    )
                ],
            )
        )

        root = paths["root"]
        self.assertTrue((root / "0_SessionContext" / "Video_Source.mp4").exists())
        self.assertTrue((root / "SessionReport").is_dir())
        for part in ("manifests", "schemes", "reports", "media", "subtitles", "json", "packages"):
            self.assertTrue((root / "SessionOutput" / part).is_dir(), part)

        variables = read_json(paths["variables"])
        input_manifest = read_json(paths["input_manifest"])
        s0_state = read_json(paths["s0_state"])
        s0_output_manifest = read_json(root / "S0_00_PrepareSessionVariables" / "Output" / "OutputManifest.json")

        self.assertEqual(variables["schema_version"], "1.0")
        self.assertEqual(variables["source_video_path"], "0_SessionContext/Video_Source.mp4")
        self.assertTrue(variables["cloud_asr_data_transfer_allowed"])
        self.assertEqual(variables["cloud_asr_data_transfer_scope"], "task_audio_to_configured_asr_provider")
        self.assertTrue(variables["cloud_asr_data_transfer_authorized_at"])
        self.assertEqual(input_manifest["files"][0]["source_ref"], "input/source.mp4")
        self.assertEqual(s0_state["status"], "completed")
        self.assertEqual(s0_output_manifest["status"], "completed")

    def test_prepare_rejects_symlink_escape_before_manifest_write(self) -> None:
        outside = self.workspace.parent / "outside-tool-session-secret.txt"
        outside.write_text("secret", encoding="utf-8")
        try:
            (self.workspace / "escape.txt").symlink_to(outside)
            with self.assertRaises(WorkspacePathError):
                prepare_session_variables(
                    PrepareSessionVariablesInput(
                        workspace_dir=self.workspace,
                        tool_use_session_id="tus_escape",
                        input_files=[PrepareInputFile(source_path="escape.txt", target_name="Copied.txt")],
                    )
                )
        finally:
            outside.unlink(missing_ok=True)

    def test_tool_session_service_emits_s0_events_without_business_scripts(self) -> None:
        sink = FakeEventSink()
        service = ToolSessionService(event_sink=sink)
        result = service.start(
            PrepareSessionVariablesInput(
                workspace_dir=self.workspace,
                workflow_id="openclip_analysis",
                task_id=123,
                attempt_id=42,
                tool_use_session_id="tus_service",
            ),
            session_id=9,
        )

        self.assertEqual(result.tool_use_session_id, "tus_service")
        self.assertTrue(result.variables_path.exists())
        self.assertEqual([event["kind"] for event in sink.events], ["tool_session.prepare.started", "tool_session.prepare.completed"])
        self.assertEqual(sink.events[1]["kwargs"]["task_id"], 123)
        self.assertEqual(sink.events[1]["kwargs"]["attempt_id"], 42)


class FakeUsageRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record(self, **kwargs):
        self.calls.append(kwargs)
        return "usage-local-1"


class FakeEventSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def add_event(self, session_id: int, kind: str, payload: dict | None = None, **kwargs):
        self.events.append({"session_id": session_id, "kind": kind, "payload": payload or {}, "kwargs": kwargs})
        return len(self.events)


class FakeToolAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, *, tool: dict, step: RunnerStep, paths, tool_dir: Path) -> ToolResult:
        self.calls += 1
        output_path = paths.session_output_dir / "reports" / f"{step.tool_id}_report.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"tool_id": step.tool_id, "idempotency_key": step.idempotency_key}), encoding="utf-8")
        return ToolResult(
            tool_id=step.tool_id,
            tool_name=step.tool_name,
            step_id=step.step_id,
            status="completed",
            outputs={"adapter": "fake"},
            result_paths=[output_path.relative_to(paths.root).as_posix()],
        )


class ContextPatchToolAdapter:
    def __init__(self, patch: dict) -> None:
        self.patch = patch

    def run(self, *, tool: dict, step: RunnerStep, paths, tool_dir: Path) -> ToolResult:
        return ToolResult(
            tool_id=step.tool_id,
            tool_name=step.tool_name,
            step_id=step.step_id,
            status="completed",
            context_patch=self.patch,
        )


class ManifestToolAdapter:
    def run(self, *, tool: dict, step: RunnerStep, paths, tool_dir: Path) -> ToolResult:
        output_path = paths.session_output_dir / "reports" / "custom_manifest_report.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("custom", encoding="utf-8")
        manifest = OutputManifest(
            tool_use_session_id=paths.tool_use_session_id,
            step_id=step.step_id,
            tool_id=step.tool_id,
            status="completed",
            files=[
                OutputFileEntry(
                    path=output_path.relative_to(paths.root).as_posix(),
                    kind="report",
                    schema_name="custom.report.v1",
                    sha256="abc123",
                    size=6,
                    visibility="public",
                    sensitivity="business",
                    downloadable=1,
                )
            ],
        )
        (tool_dir / "Output").mkdir(parents=True, exist_ok=True)
        (tool_dir / "Output" / "OutputManifest.json").write_text(json.dumps(manifest.model_dump()), encoding="utf-8")
        return ToolResult(
            tool_id=step.tool_id,
            tool_name=step.tool_name,
            step_id=step.step_id,
            status="completed",
            result_paths=[output_path.relative_to(paths.root).as_posix()],
        )


class SleepingToolAdapter:
    def run(self, *, tool: dict, step: RunnerStep, paths, tool_dir: Path) -> ToolResult:
        time.sleep(0.08)
        return ToolResult(tool_id=step.tool_id, tool_name=step.tool_name, step_id=step.step_id, status="completed")


class FakeSessionFileRepo:
    def __init__(self) -> None:
        self.files: list[dict] = []

    def upsert_file(self, session_id: int, path: str, kind: str, size: int, origin: str, downloadable: int, updated_at: int, **kwargs):
        self.files.append(
            {
                "session_id": session_id,
                "path": path,
                "kind": kind,
                "size": size,
                "origin": origin,
                "downloadable": downloadable,
                "updated_at": updated_at,
                **kwargs,
            }
        )


class ModelBrokerContractTest(unittest.TestCase):
    def test_broker_requires_runner_idempotency_key_and_records_local_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = FakeUsageRecorder()
            broker = ModelBroker(usage_recorder=recorder)
            audit_path = Path(tmp) / "Prompt" / "ModelCall_request-1.json"

            response = broker.submit_model_call(
                ModelBrokerRequest(
                    provider="openai",
                    model_id="gpt-test",
                    modality="text",
                    request_id="request-1",
                    idempotency_key="tus_contract:S1:0",
                    tool_use_session_id="tus_contract",
                    step_index=1,
                    step_id="S1",
                    tool_name="SemanticLLMStructureBuilder",
                    task_id=123,
                    attempt_id=42,
                    audit_path=audit_path,
                ),
                fake_response={
                    "status": "ok",
                    "summary": "Authorization: Bearer sk-secret Cookie: a=b mihomo://subscription",
                    "usage": {"input_tokens": 10, "output_tokens": 2, "api_key": "sk-usage-secret"},
                },
            )
            replay = broker.submit_model_call(
                ModelBrokerRequest(
                    provider="openai",
                    model_id="gpt-test",
                    modality="text",
                    request_id="request-retry",
                    idempotency_key="tus_contract:S1:0",
                    audit_path=audit_path,
                ),
                fake_response={"status": "ok", "usage": {"input_tokens": 999}},
            )
            audit_json = read_json(audit_path)

        self.assertEqual(response.local_usage_id, "usage-local-1")
        self.assertEqual(replay.request_id, "request-1")
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(response.audit.schema_version, "1.0")
        self.assertEqual(response.audit.request_id, "request-1")
        self.assertEqual(response.audit.local_usage_id, "usage-local-1")
        self.assertEqual(response.audit.idempotency_key, "tus_contract:S1:0")
        self.assertEqual(response.audit.step_id, "S1")
        self.assertEqual(audit_json["idempotency_key"], "tus_contract:S1:0")
        self.assertEqual(audit_json["step_id"], "S1")
        serialized_audit = json.dumps(audit_json)
        self.assertNotIn("sk-", serialized_audit)
        self.assertNotIn("Authorization", serialized_audit)
        self.assertNotIn("Cookie", serialized_audit)
        self.assertNotIn("mihomo://", serialized_audit)
        self.assertEqual(recorder.calls[0]["proxy_policy"], "mihomo")
        self.assertEqual(recorder.calls[0]["provider_mode"], "local_box")
        self.assertEqual(recorder.calls[0]["billing_mode"], "local_usage_only")
        self.assertEqual(recorder.calls[0]["task_id"], 123)
        self.assertEqual(recorder.calls[0]["attempt_id"], 42)
        self.assertEqual(recorder.calls[0]["step_id"], "S1")
        self.assertEqual(recorder.calls[0]["idempotency_key"], "tus_contract:S1:0")

        with self.assertRaises(ValueError):
            broker.submit_model_call(
                ModelBrokerRequest(
                    provider="openai",
                    model_id="gpt-test",
                    modality="text",
                    request_id="request-2",
                    idempotency_key="",
                )
            )


class RunnerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        prepare_session_variables(
            PrepareSessionVariablesInput(
                workspace_dir=self.workspace,
                tool_use_session_id="tus_runner",
                attempt_id=7,
            )
        )
        self.runner = ToolSessionRunner(
            workspace_dir=self.workspace,
            tool_use_session_id="tus_runner",
            attempt_id=7,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_noop_runner_writes_step_state_summary_and_bumps_rerun_key(self) -> None:
        first = self.runner.run_noop("S1")
        first_key = first.outputs["idempotency_key"]
        second = self.runner.run_noop("S1", force_rerun=True)
        second_key = second.outputs["idempotency_key"]

        root = self.workspace / "tool_use_sessions" / "tus_runner"
        state = read_json(root / "S1_NoopTool" / "State.json")
        summary = read_json(root / "SessionReport" / "SessionRunSummary.json")

        self.assertEqual(first_key, "tus_runner:S1:0")
        self.assertEqual(second_key, "tus_runner:S1:1")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["retry_count"], 1)
        self.assertEqual(summary["status"], "running")
        self.assertEqual(summary["steps"][0]["idempotency_key"], "tus_runner:S1:1")

        restored = ToolSessionRunner.from_summary(workspace_dir=self.workspace, tool_use_session_id="tus_runner")
        self.assertEqual(restored.steps["S1"].retry_count, 1)
        self.assertEqual(restored.steps["S1"].idempotency_key, "tus_runner:S1:1")

    def test_context_patch_is_limited_by_declared_variable_ownership(self) -> None:
        updated = self.runner.merge_context_patch(
            SessionContextPatch(tool_id="noop", step_id="S1", patch={"selected_scheme": "balanced"}),
            allowed_fields={"selected_scheme"},
        )

        self.assertEqual(updated["selected_scheme"], "balanced")
        with self.assertRaises(ValueError):
            self.runner.merge_context_patch(
                SessionContextPatch(tool_id="noop", step_id="S1", patch={"run_model_id": "unauthorized"}),
                allowed_fields={"selected_scheme"},
            )

    def test_context_patch_schema_failure_rolls_back_variables(self) -> None:
        variables_path = self.workspace / "tool_use_sessions" / "tus_runner" / "0_SessionContext" / "Variables.json"
        before = read_json(variables_path)

        with self.assertRaises(ValidationError):
            self.runner.merge_context_patch(
                SessionContextPatch(tool_id="noop", step_id="S1", patch={"current_attempt_id": "not-an-int"}),
                allowed_fields={"current_attempt_id"},
            )

        self.assertEqual(read_json(variables_path), before)

    def test_completed_with_sync_error_can_resync_result_index(self) -> None:
        root = self.workspace / "tool_use_sessions" / "tus_runner"
        result_index = root / "SessionOutput" / "manifests" / "result_index.json"
        result_index.write_text(json.dumps(ResultIndex(tool_use_session_id="tus_runner").model_dump()), encoding="utf-8")

        self.runner.mark_completed_with_sync_error()
        summary_before = read_json(root / "SessionReport" / "SessionRunSummary.json")
        result = self.runner.resync()
        summary_after = read_json(root / "SessionReport" / "SessionRunSummary.json")

        self.assertEqual(summary_before["status"], "completed_with_sync_error")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.synced_files, ["SessionOutput/manifests/result_index.json"])
        self.assertEqual(summary_after["status"], "completed")

    def test_running_step_can_be_marked_stale(self) -> None:
        self.runner.steps["S2"] = RunnerStep(
            step_id="S2",
            tool_id="slow",
            tool_name="SlowTool",
            status="running",
            heartbeat_at="2026-05-28T00:00:00+00:00",
        )

        self.assertTrue(self.runner.mark_stale_running("S2"))
        summary = read_json(self.workspace / "tool_use_sessions" / "tus_runner" / "SessionReport" / "SessionRunSummary.json")
        self.assertEqual(summary["steps"][0]["status"], "stale_running")

    def test_heartbeat_timeout_recovers_stale_running_step_and_emits_event(self) -> None:
        sink = FakeEventSink()
        runner = ToolSessionRunner(
            workspace_dir=self.workspace,
            tool_use_session_id="tus_runner",
            attempt_id=7,
            heartbeat_timeout_seconds=300,
            session_id=9,
            event_sink=sink,
        )
        runner.steps["S3"] = RunnerStep(
            step_id="S3",
            tool_id="slow",
            tool_name="SlowTool",
            status="running",
            heartbeat_at="2026-05-28T00:00:00+00:00",
            heartbeat_timeout_seconds=300,
        )
        runner.steps["S4"] = RunnerStep(
            step_id="S4",
            tool_id="very_slow",
            tool_name="VerySlowTool",
            status="running",
            heartbeat_at="2026-05-28T00:00:00+00:00",
            heartbeat_timeout_seconds=900,
        )

        recovered = runner.recover_stale_running_steps(now=datetime(2026, 5, 28, 0, 6, tzinfo=timezone.utc))

        self.assertEqual(recovered, ["S3"])
        self.assertEqual(runner.steps["S4"].status, "running")
        self.assertEqual(sink.events[0]["kind"], "tool_session.step.stale_running")
        self.assertEqual(sink.events[0]["payload"]["status"], "stale_running")

    def test_registry_step_uses_adapter_and_never_invokes_business_script_directly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "input").mkdir()
            (workspace / "input" / "source.mp4").write_bytes(b"fake-video")
            prepare_session_variables(
                PrepareSessionVariablesInput(
                    workspace_dir=workspace,
                    tool_use_session_id="tus_registry",
                    attempt_id=8,
                    input_files=[PrepareInputFile(source_path="input/source.mp4", target_name="Video_Source.mp4")],
                )
            )
            normalized = normalize_registry_file(REGISTRY_PATH, strict=True, tool_ids={"01"})
            runner = ToolSessionRunner(workspace_dir=workspace, tool_use_session_id="tus_registry", attempt_id=8)
            adapter = FakeToolAdapter()

            result = runner.run_registry_step(
                step_id="S1",
                tool_id="01",
                step_index=1,
                normalized_registry=normalized,
                adapters={"01": adapter},
            )
            summary = read_json(workspace / "tool_use_sessions" / "tus_registry" / "SessionReport" / "SessionRunSummary.json")

            self.assertEqual(result.status, "completed")
            self.assertEqual(adapter.calls, 1)
            self.assertEqual(summary["steps"][0]["tool_id"], "01")
            self.assertEqual(summary["steps"][0]["heartbeat_timeout_seconds"], 300)
            self.assertTrue((workspace / "tool_use_sessions" / "tus_registry" / "SessionOutput" / "reports" / "01_report.json").exists())

    def test_registry_step_applies_declared_context_patch_and_fails_denied_patch(self) -> None:
        registry = {
            "schema_version": "1.0",
            "tools": [
                {
                    "id": "patcher",
                    "tool_id": "patcher",
                    "tool_name": "PatchTool",
                    "consumes_outputs": [],
                    "reads_session_context": [],
                    "runtime_dependencies": [],
                    "writes_session_context": ["selected_scheme"],
                    "heartbeat_timeout_seconds": 300,
                }
            ],
        }

        ok = self.runner.run_registry_step(
            step_id="S8",
            tool_id="patcher",
            step_index=8,
            normalized_registry=registry,
            adapters={"patcher": ContextPatchToolAdapter({"selected_scheme": "balanced"})},
        )
        denied = self.runner.run_registry_step(
            step_id="S9",
            tool_id="patcher",
            step_index=9,
            normalized_registry=registry,
            adapters={"patcher": ContextPatchToolAdapter({"run_model_id": "not-owned"})},
        )
        variables = read_json(self.workspace / "tool_use_sessions" / "tus_runner" / "0_SessionContext" / "Variables.json")

        self.assertEqual(ok.status, "completed")
        self.assertEqual(variables["selected_scheme"], "balanced")
        self.assertEqual(denied.status, "failed")
        self.assertIn("undeclared fields", denied.errors[0])

    def test_runtime_environment_hint_does_not_block_dependency_check(self) -> None:
        result = self.runner.check_dependencies(
            {
                "reads_session_context": [],
                "consumes_outputs": [],
                "runtime_dependencies": ["gpu_or_mps_for_speed"],
            }
        )

        self.assertEqual(result.status, "ready")

    def test_adapter_output_manifest_is_preserved(self) -> None:
        registry = {
            "schema_version": "1.0",
            "tools": [
                {
                    "id": "manifest",
                    "tool_id": "manifest",
                    "tool_name": "ManifestTool",
                    "consumes_outputs": [],
                    "reads_session_context": [],
                    "runtime_dependencies": [],
                    "heartbeat_timeout_seconds": 300,
                }
            ],
        }

        result = self.runner.run_registry_step(step_id="S10", tool_id="manifest", step_index=10, normalized_registry=registry, adapters={"manifest": ManifestToolAdapter()})
        manifest = OutputManifest.model_validate(read_json(self.workspace / "tool_use_sessions" / "tus_runner" / "S10_ManifestTool" / "Output" / "OutputManifest.json"))

        self.assertEqual(result.status, "completed")
        self.assertEqual(manifest.files[0].sha256, "abc123")
        self.assertEqual(manifest.files[0].schema_name, "custom.report.v1")
        self.assertEqual(manifest.files[0].sensitivity, "business")

    def test_background_heartbeat_updates_while_adapter_blocks(self) -> None:
        registry = {
            "schema_version": "1.0",
            "tools": [
                {
                    "id": "sleep",
                    "tool_id": "sleep",
                    "tool_name": "SleepTool",
                    "consumes_outputs": [],
                    "reads_session_context": [],
                    "runtime_dependencies": [],
                    "heartbeat_timeout_seconds": 300,
                }
            ],
        }
        runner = ToolSessionRunner(
            workspace_dir=self.workspace,
            tool_use_session_id="tus_runner",
            attempt_id=7,
            heartbeat_interval_seconds=0.01,
        )

        runner.run_registry_step(step_id="S11", tool_id="sleep", step_index=11, normalized_registry=registry, adapters={"sleep": SleepingToolAdapter()})
        summary = read_json(self.workspace / "tool_use_sessions" / "tus_runner" / "SessionReport" / "SessionRunSummary.json")

        self.assertGreaterEqual(len(summary["steps"][0]["heartbeat_at"]), 10)
        self.assertEqual(summary["steps"][0]["status"], "completed")

    def test_subprocess_tool_adapter_runs_migrated_cli_contract_without_business_scripts(self) -> None:
        script = self.workspace / "fake_tool.py"
        script.write_text(
            "\n".join(
                [
                    "import json",
                    "import sys",
                    "from opcrew_backend.tool_sessions.schemas import ToolResult",
                    "step_id = sys.argv[sys.argv.index('--step-id') + 1]",
                    "tool_id = sys.argv[sys.argv.index('--tool-id') + 1]",
                    "result = ToolResult(tool_id=tool_id, tool_name='SubprocessFake', step_id=step_id, status='completed')",
                    "print(json.dumps(result.model_dump()))",
                ]
            ),
            encoding="utf-8",
        )
        registry = {
            "schema_version": "1.0",
            "tools": [
                {
                    "id": "subprocess",
                    "tool_id": "subprocess",
                    "tool_name": "SubprocessFake",
                    "script": str(script),
                    "consumes_outputs": [],
                    "reads_session_context": [],
                    "runtime_dependencies": [],
                    "heartbeat_timeout_seconds": 300,
                }
            ],
        }

        result = self.runner.run_registry_step(
            step_id="S12",
            tool_id="subprocess",
            step_index=12,
            normalized_registry=registry,
            adapters={"subprocess": SubprocessToolAdapter(python_executable=sys.executable)},
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_id, "subprocess")

    def test_subprocess_tool_adapter_scrubs_parent_secret_environment(self) -> None:
        script = self.workspace / "env_tool.py"
        script.write_text(
            "\n".join(
                [
                    "import json",
                    "import os",
                    "import sys",
                    "secret_names = ['OPENAI_API_KEY', 'AUTHORIZATION', 'COOKIE', 'HTTPS_PROXY', 'DATABASE_URL']",
                    "leaked = {name: os.environ[name] for name in secret_names if os.environ.get(name)}",
                    "step_id = sys.argv[sys.argv.index('--step-id') + 1]",
                    "tool_id = sys.argv[sys.argv.index('--tool-id') + 1]",
                    "print(json.dumps({'tool_id': tool_id, 'tool_name': 'EnvTool', 'step_id': step_id, 'status': 'completed', 'outputs': {'leaked': leaked, 'has_path': bool(os.environ.get('PATH')), 'pythonpath': os.environ.get('PYTHONPATH', '')}}))",
                ]
            ),
            encoding="utf-8",
        )
        secret_names = ["OPENAI_API_KEY", "AUTHORIZATION", "COOKIE", "HTTPS_PROXY", "DATABASE_URL"]
        previous = {name: os.environ.get(name) for name in secret_names}
        try:
            os.environ["OPENAI_API_KEY"] = "sk-parent-secret"
            os.environ["AUTHORIZATION"] = "Bearer parent-secret"
            os.environ["COOKIE"] = "sid=parent-secret"
            os.environ["HTTPS_PROXY"] = "http://user:parent-secret@example.test"
            os.environ["DATABASE_URL"] = "postgresql://user:parent-secret@example.test/db"
            registry = {
                "schema_version": "1.0",
                "tools": [
                    {
                        "id": "env",
                        "tool_id": "env",
                        "tool_name": "EnvTool",
                        "script": str(script),
                        "consumes_outputs": [],
                        "reads_session_context": [],
                        "runtime_dependencies": [],
                        "heartbeat_timeout_seconds": 300,
                    }
                ],
            }

            result = self.runner.run_registry_step(
                step_id="S13",
                tool_id="env",
                step_index=13,
                normalized_registry=registry,
                adapters={"env": SubprocessToolAdapter(python_executable=sys.executable)},
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.outputs["leaked"], {})
        self.assertTrue(result.outputs["has_path"])
        pythonpath = str(result.outputs["pythonpath"]).split(os.pathsep)
        self.assertEqual(pythonpath[0], str(REPO_ROOT / "backend"))
        self.assertIn(str(REPO_ROOT), pythonpath)
        self.assertIn(str(REPO_ROOT.parent), pythonpath)

    def test_subprocess_env_includes_data_dir_and_prefers_managed_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_runtime = Path(tmp) / "analysis_v1_py312"
            fake_python = fake_runtime / "bin" / "python"
            fake_python.parent.mkdir(parents=True)
            fake_python.write_text("#!/bin/sh\n", encoding="utf-8")
            data_dir = Path(tmp) / "data"
            parent_env = {
                "HOME": str(Path(tmp) / "home"),
                "PATH": os.environ.get("PATH", ""),
                "OPENCREW_DATA_DIR": str(data_dir),
                "OPENCREW_ANALYSIS_V1_RUNTIME_DIR": str(fake_runtime),
            }

            env = _scrub_subprocess_env(parent_env=parent_env, extra_env={}, repo_root=REPO_ROOT)
            selected = _select_subprocess_python(configured_python=None, parent_env=parent_env)

        self.assertEqual(env["OPENCREW_DATA_DIR"], str(data_dir))
        self.assertEqual(selected, str(fake_python))
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(REPO_ROOT / "backend"))

    def test_subprocess_tool_adapter_rejects_sensitive_extra_env(self) -> None:
        script = self.workspace / "extra_env_tool.py"
        script.write_text(
            "\n".join(
                [
                    "import json",
                    "import sys",
                    "step_id = sys.argv[sys.argv.index('--step-id') + 1]",
                    "tool_id = sys.argv[sys.argv.index('--tool-id') + 1]",
                    "print(json.dumps({'tool_id': tool_id, 'tool_name': 'ExtraEnvTool', 'step_id': step_id, 'status': 'completed'}))",
                ]
            ),
            encoding="utf-8",
        )
        registry = {
            "schema_version": "1.0",
            "tools": [
                {
                    "id": "extra-env",
                    "tool_id": "extra-env",
                    "tool_name": "ExtraEnvTool",
                    "script": str(script),
                    "consumes_outputs": [],
                    "reads_session_context": [],
                    "runtime_dependencies": [],
                    "heartbeat_timeout_seconds": 300,
                }
            ],
        }

        result = self.runner.run_registry_step(
            step_id="S14",
            tool_id="extra-env",
            step_index=14,
            normalized_registry=registry,
            adapters={"extra-env": SubprocessToolAdapter(python_executable=sys.executable, extra_env={"OPENAI_API_KEY": "sk-extra"})},
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("Refusing to pass sensitive environment variable", result.errors[0])

    def test_registry_step_blocks_without_adapter_or_hard_dependency(self) -> None:
        registry = {
            "schema_version": "1.0",
            "tools": [
                {
                    "id": "needs-upstream",
                    "tool_id": "needs-upstream",
                    "tool_name": "NeedsUpstream",
                    "consumes_outputs": [{"tool_id": "01", "path": ""}],
                    "reads_session_context": [],
                    "runtime_dependencies": [],
                    "heartbeat_timeout_seconds": 300,
                }
            ],
        }
        adapter = FakeToolAdapter()

        result = self.runner.run_registry_step(
            step_id="S5",
            tool_id="needs-upstream",
            step_index=5,
            normalized_registry=registry,
            adapters={"needs-upstream": adapter},
        )
        no_adapter = self.runner.run_registry_step(
            step_id="S6",
            tool_id="needs-upstream",
            step_index=6,
            normalized_registry={**registry, "tools": [{**registry["tools"][0], "consumes_outputs": []}]},
            adapters={},
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(adapter.calls, 0)
        self.assertIn("Run upstream tool 01", result.errors[0])
        self.assertEqual(no_adapter.status, "blocked")
        self.assertIn("No adapter registered", no_adapter.errors[0])

    def test_finalize_writes_index_and_session_file_rows(self) -> None:
        normalized = {
            "schema_version": "1.0",
            "tools": [
                {
                    "id": "fake",
                    "tool_id": "fake",
                    "tool_name": "FakeTool",
                    "consumes_outputs": [],
                    "reads_session_context": [],
                    "runtime_dependencies": [],
                    "heartbeat_timeout_seconds": 300,
                }
            ],
        }
        adapter = FakeToolAdapter()
        self.runner.run_registry_step(step_id="S7", tool_id="fake", step_index=7, normalized_registry=normalized, adapters={"fake": adapter})
        self.runner.session_id = 9
        repo = FakeSessionFileRepo()
        sync = ToolSessionResultSync(session_repo=repo, clock_ms=lambda: 123)

        result = self.runner.finalize_session(result_syncer=sync)

        paths = {row["path"]: row for row in repo.files}
        rebuilt = ResultIndex.model_validate(read_json(self.workspace / "tool_use_sessions" / "tus_runner" / "SessionOutput" / "manifests" / "result_index.json"))
        self.assertEqual(result.status, "completed")
        self.assertEqual(rebuilt.tool_use_session_id, "tus_runner")
        self.assertIn("SessionOutput/manifests/result_index.json", result.synced_files)
        self.assertIn("SessionOutput/reports/fake_report.json", result.synced_files)
        report_row = paths["tool_use_sessions/tus_runner/SessionOutput/reports/fake_report.json"]
        index_row = paths["tool_use_sessions/tus_runner/SessionOutput/manifests/result_index.json"]
        self.assertEqual(report_row["tool_use_session_id"], "tus_runner")
        self.assertEqual(report_row["attempt_id"], 7)
        self.assertEqual(report_row["downloadable"], 1)
        self.assertEqual(index_row["visibility"], "internal")
        self.assertEqual(index_row["downloadable"], 0)

    def test_finalize_preserves_requested_blocked_terminal_status(self) -> None:
        self.runner.session_id = 9
        result = self.runner.finalize_session(
            result_syncer=ToolSessionResultSync(session_repo=FakeSessionFileRepo(), clock_ms=lambda: 123),
            terminal_status="blocked",
        )

        summary = read_json(
            self.workspace / "tool_use_sessions" / "tus_runner" / "SessionReport" / "SessionRunSummary.json"
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(summary["status"], "blocked")

    def test_completed_terminal_status_exposes_result_sync_failure(self) -> None:
        class FailedSyncer:
            def resync_tool_session(self, **_kwargs):
                return ResyncResult(
                    tool_use_session_id="tus_runner",
                    attempt_id=7,
                    status="failed",
                    errors=["session_files unavailable"],
                )

        result = self.runner.finalize_session(
            result_syncer=FailedSyncer(),
            terminal_status="completed",
        )

        summary = read_json(
            self.workspace / "tool_use_sessions" / "tus_runner" / "SessionReport" / "SessionRunSummary.json"
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(summary["status"], "completed_with_sync_error")


if __name__ == "__main__":
    unittest.main()
