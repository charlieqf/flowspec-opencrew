from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_executor_module():
    path = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py"
    spec = importlib.util.spec_from_file_location("analysis_v1_05_02_reference_provider_contract", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1VideoPlanImageReferenceProviderContractTest(unittest.TestCase):
    def test_target_frame_plus_host_reference_keeps_selected_xai_provider(self) -> None:
        module = load_executor_module()
        args = SimpleNamespace(database_url="postgresql://test")
        variables = {
            "default_image_config": {
                "provider": "xai",
                "model": "grok-imagine-image-quality",
            }
        }
        references = [
            {"role": "TARGET_FRAME", "kind": "target_frame", "working_path": "target.jpg"},
            {"role": "HOST_REFERENCE", "kind": "host", "working_path": "host.png"},
        ]
        selection, assessment = module.image_provider_selection_for_references(args, variables, references)

        self.assertEqual(selection, {"kind": "image", "provider": "xai", "model": "grok-imagine-image-quality"})
        self.assertEqual(assessment["provider"], "xai/grok-imagine-image-quality")
        self.assertEqual(assessment["reason"], "selected_image_provider_used_without_automatic_fallback")
        self.assertTrue(assessment["requires_manual_quality_review"])
        self.assertIn("HOST_REFERENCE", assessment["reference_roles"])

    def test_target_frame_only_keeps_selected_xai_provider(self) -> None:
        module = load_executor_module()
        args = SimpleNamespace(database_url="postgresql://test")
        variables = {
            "default_image_config": {
                "provider": "xai",
                "model": "grok-imagine-image-quality",
            }
        }
        references = [{"role": "TARGET_FRAME", "kind": "target_frame", "working_path": "target.jpg"}]

        selection, assessment = module.image_provider_selection_for_references(args, variables, references)

        self.assertEqual(selection, {"kind": "image", "provider": "xai", "model": "grok-imagine-image-quality"})
        self.assertEqual(assessment, {})

    def test_openai_selected_provider_keeps_openai_with_review_assessment(self) -> None:
        module = load_executor_module()
        args = SimpleNamespace(database_url="postgresql://test")
        variables = {
            "default_image_config": {
                "provider": "openai",
                "model": "gpt-image-1.5",
            }
        }
        references = [
            {"role": "TARGET_FRAME", "kind": "target_frame", "working_path": "target.jpg"},
            {"role": "HOST_REFERENCE", "kind": "host", "working_path": "host.png"},
        ]

        selection, assessment = module.image_provider_selection_for_references(args, variables, references)

        self.assertEqual(selection, {"kind": "image", "provider": "openai", "model": "gpt-image-1.5"})
        self.assertEqual(assessment["provider"], "openai/gpt-image-1.5")
        self.assertTrue(assessment["requires_manual_quality_review"])


if __name__ == "__main__":
    unittest.main()
