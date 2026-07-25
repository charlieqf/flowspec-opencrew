from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from opcrew_backend.tool_sessions.prepare import PrepareInputFile, PrepareSessionVariablesInput, prepare_session_variables  # noqa: E402
from opcrew_backend.tool_sessions.registry_normalizer import normalize_registry_file  # noqa: E402
from opcrew_backend.tool_sessions.runner import SubprocessToolAdapter, ToolSessionRunner  # noqa: E402
from opcrew_backend.tool_sessions.schemas import OutputManifest, ToolResult  # noqa: E402


ANALYSIS_V1_REGISTRY = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "tool_registry.json"


def read_json(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


class AnalysisV1FrameworkBridgeContractTest(unittest.TestCase):
    def test_analysis_v1_registry_normalizes_without_manual_overrides(self) -> None:
        normalized = normalize_registry_file(ANALYSIS_V1_REGISTRY, strict=True)

        tool_ids = [tool["id"] for tool in normalized["tools"]]
        self.assertEqual(tool_ids, ["00", "01", "02_01", "02_02", "03_01", "03_02", "03_03", "04_01", "04_01_free", "04_02", "04_03", "05_01", "05_02", "05_03", "05_04", "05_05", "05_06", "06_01"])
        self.assertEqual(normalized["unresolved_dependencies"], [])
        tool_01 = next(tool for tool in normalized["tools"] if tool["id"] == "01")
        self.assertEqual(tool_01["reads_session_context"], ["source_video"])
        tool_03_02 = next(tool for tool in normalized["tools"] if tool["id"] == "03_02")
        self.assertTrue(tool_03_02["uses_tts"])
        self.assertEqual(tool_03_02["model_requirements"]["call_path"], "broker_resolver")
        optional_kinds = {item["kind"] for item in tool_03_02["optional_dependencies"]}
        self.assertTrue({"data_asset", "python_package"}.issubset(optional_kinds))
        tool_05_01 = next(tool for tool in normalized["tools"] if tool["id"] == "05_01")
        self.assertEqual(tool_05_01["consumes_outputs"][0]["path"], "SessionOutput/storyboard/srt_storyboard.json")
        tool_06_01 = next(tool for tool in normalized["tools"] if tool["id"] == "06_01")
        self.assertEqual(tool_06_01["script"], "ToolLibrary/Analysis_V1/06_01_VideoPlanComposer.py")

    def test_framework_bridge_returns_tool_result_and_manifest_for_prepare_and_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "input").mkdir(parents=True)
            (workspace / "input" / "source.mp4").write_bytes(b"not-a-real-video")
            prepare_session_variables(
                PrepareSessionVariablesInput(
                    workspace_dir=workspace,
                    workflow_id="analysis_v1_contract",
                    tool_use_session_id="tus_analysis_v1_contract",
                    input_files=[PrepareInputFile(source_path="input/source.mp4", target_name="Video_Source.mp4")],
                )
            )
            normalized = normalize_registry_file(ANALYSIS_V1_REGISTRY, strict=True)
            adapter = SubprocessToolAdapter(python_executable=sys.executable, repo_root=REPO_ROOT)
            runner = ToolSessionRunner(workspace_dir=workspace, tool_use_session_id="tus_analysis_v1_contract", heartbeat_interval_seconds=0.01)

            prepare_result = runner.run_registry_step(
                step_id="S0",
                tool_id="00",
                step_index=0,
                normalized_registry=normalized,
                adapters={"*": adapter},
            )
            probe_result = runner.run_registry_step(
                step_id="S1",
                tool_id="01",
                step_index=1,
                normalized_registry=normalized,
                adapters={"*": adapter},
            )

            self.assertIsInstance(prepare_result, ToolResult)
            self.assertEqual(prepare_result.status, "completed")
            self.assertIn(probe_result.status, {"completed", "blocked"})
            self.assertEqual(probe_result.tool_id, "01")
            self.assertEqual(probe_result.step_id, "S1")

            root = workspace / "tool_use_sessions" / "tus_analysis_v1_contract"
            prepare_manifest = OutputManifest.model_validate(read_json(root / "S0_00_PrepareSessionVariables" / "Output" / "OutputManifest.json"))
            probe_manifest = OutputManifest.model_validate(read_json(root / "S1_01_VideoProbeMetadata" / "Output" / "OutputManifest.json"))
            self.assertEqual(prepare_manifest.tool_id, "00")
            self.assertEqual(probe_manifest.tool_id, "01")
            self.assertTrue((root / "S1_01_VideoProbeMetadata" / "Report" / "legacy_result.json").exists())

    def test_framework_bridge_invokes_video_plan_composer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            prepare_session_variables(
                PrepareSessionVariablesInput(
                    workspace_dir=workspace,
                    workflow_id="analysis_v1_contract",
                    tool_use_session_id="tus_analysis_v1_composer_contract",
                )
            )
            root = workspace / "tool_use_sessions" / "tus_analysis_v1_composer_contract"
            (root / "SessionOutput" / "storyboard").mkdir(parents=True, exist_ok=True)
            (root / "SessionOutput" / "storyboard" / "srt_storyboard.json").write_text('{"shots":[]}', encoding="utf-8")
            (root / "SessionOutput" / "storyboard" / "video_generation_plan.json").write_text('{"shots":[]}', encoding="utf-8")

            normalized = normalize_registry_file(ANALYSIS_V1_REGISTRY, strict=True)
            adapter = SubprocessToolAdapter(python_executable=sys.executable, repo_root=REPO_ROOT)
            runner = ToolSessionRunner(workspace_dir=workspace, tool_use_session_id="tus_analysis_v1_composer_contract", heartbeat_interval_seconds=0.01)
            result = runner.run_registry_step(
                step_id="S10",
                tool_id="06_01",
                step_index=10,
                normalized_registry=normalized,
                adapters={"*": adapter},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.tool_id, "06_01")
            manifest = OutputManifest.model_validate(read_json(root / "S10_06_01_VideoPlanComposer" / "Output" / "OutputManifest.json"))
            self.assertEqual(manifest.tool_id, "06_01")
            self.assertEqual(manifest.status, "blocked")


if __name__ == "__main__":
    unittest.main()
