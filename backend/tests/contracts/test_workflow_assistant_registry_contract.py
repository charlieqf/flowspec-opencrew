from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, REPO_ROOT / "backend"):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from WorkflowAssistant.backend.workflow_assistant.workflow_config import (  # noqa: E402
    WORKFLOW_CONFIGS,
    validate_workflow_tool_library,
    workflow_tool_library_source,
)


class WorkflowAssistantRegistryContractTest(unittest.TestCase):
    def test_openclip_analysis_resolves_analysis_registry(self) -> None:
        source = workflow_tool_library_source("openclip_analysis")

        self.assertEqual(source["workflow_id"], "openclip_analysis")
        self.assertEqual(Path(source["root"]).name, "Analysis")
        self.assertEqual(Path(source["registry"]).name, "tool_registry.json")
        self.assertTrue(source["registry"].endswith("ToolLibrary/Analysis/tool_registry.json"))

    def test_all_oc_rebuild_variants_resolve_rebuild_v1_registry(self) -> None:
        workflow_ids = [
            "oc_rebuild",
            "oc_rebuild_plan_a_phase_batch_v0",
            "oc_rebuild_plan_a_shot_first_v1",
        ]

        for workflow_id in workflow_ids:
            with self.subTest(workflow_id=workflow_id):
                source = workflow_tool_library_source(workflow_id)
                self.assertEqual(Path(source["root"]).name, "Rebuild_V1")
                self.assertTrue(source["registry"].endswith("ToolLibrary/Rebuild_V1/tool_registry.json"))
                self.assertTrue(source["agent_guide"].endswith("ToolLibrary/Rebuild_V1/README.md"))

    def test_oc_rebuild_configs_do_not_point_to_legacy_rebuild_registry(self) -> None:
        for workflow_id, config in WORKFLOW_CONFIGS.items():
            if config.get("task_adapter") != "oc_rebuild":
                continue
            tool_library = config.get("tool_library") or {}
            combined = "\n".join(str(tool_library.get(key) or "") for key in ("root", "registry", "agent_guide"))
            with self.subTest(workflow_id=workflow_id):
                self.assertNotIn("ToolLibrary/Rebuild/", combined)
                self.assertIn("ToolLibrary/Rebuild_V1", combined)

    def test_missing_configured_registry_fails_without_analysis_fallback(self) -> None:
        config = copy.deepcopy(WORKFLOW_CONFIGS["oc_rebuild"])
        config["tool_library"]["root"] = "OpenCrew/ToolLibrary/Missing_Rebuild"
        config["tool_library"]["registry"] = "OpenCrew/ToolLibrary/Missing_Rebuild/tool_registry.json"
        config["tool_library"]["agent_guide"] = "OpenCrew/ToolLibrary/Missing_Rebuild/README.md"

        with self.assertRaisesRegex(RuntimeError, "Missing_Rebuild"):
            validate_workflow_tool_library("oc_rebuild_missing", config)


if __name__ == "__main__":
    unittest.main()
