from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1"
FRONTEND_MODEL = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardModel.js"


def load_analysis_module(filename: str, module_name: str):
    path = ANALYSIS_PATH / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VPG = load_analysis_module("05_01_VideoPlanGenerator.py", "analysis_v1_0501_dialogue_asset_key_contract")
VPE = load_analysis_module("05_02_VideoPlanExecutor.py", "analysis_v1_0502_dialogue_asset_key_contract")
IPG = load_analysis_module("05_03_ImagePlanGenerator.py", "analysis_v1_0503_dialogue_asset_key_contract")
VOPG = load_analysis_module("05_05_VideoOnlyPlanGenerator.py", "analysis_v1_0505_dialogue_asset_key_contract")


class KouboStoryboardDialogueAssetKeyContractTest(unittest.TestCase):
    def test_video_plan_dialogue_key_uses_only_asset_key(self) -> None:
        dialogue = {
            "srt_id": "srt_0004",
            "dialogue_id": "scene_001_dialogue_004",
            "dialogue_asset_key": "dak_target",
        }
        self.assertEqual(VPG.dialogue_key(dialogue, 4), "dak_target")
        with self.assertRaises(VPG.BlockedError):
            VPG.dialogue_key({"srt_id": "srt_0004", "dialogue_id": "scene_001_dialogue_004"}, 4)

    def test_executor_indexes_and_binds_by_asset_key_only(self) -> None:
        storyboard = {
            "shots": [{
                "scenes": [{
                    "dialogue_items": [{
                        "srt_id": "srt_0004",
                        "dialogue_id": "scene_001_dialogue_004",
                        "dialogue_asset_key": "dak_target",
                        "working_assets": {},
                    }]
                }]
            }]
        }
        index = VPE.flatten_dialogues(storyboard)
        self.assertIn("dak_target", index)
        self.assertNotIn("srt_0004", index)
        self.assertNotIn("scene_001_dialogue_004", index)

        self.assertFalse(VPE.bind_segment_output_to_storyboard(
            {"dialogue_ids": ["scene_001_dialogue_004"]},
            index,
            "video",
            "SessionOutput/storyboard/Working/dak_target_Video_Final.mp4",
        ))
        self.assertTrue(VPE.bind_segment_output_to_storyboard(
            {"dialogue_asset_keys": ["dak_target"], "dialogue_ids": ["scene_001_dialogue_004"]},
            index,
            "video",
            "SessionOutput/storyboard/Working/dak_target_Video_Final.mp4",
        ))
        dialogue = storyboard["shots"][0]["scenes"][0]["dialogue_items"][0]
        self.assertEqual(dialogue["working_assets"]["video"]["path"], "SessionOutput/storyboard/Working/dak_target_Video_Final.mp4")

    def test_derived_plans_prefer_dialogue_asset_keys(self) -> None:
        segment = {
            "asset_key": "segment_old",
            "dialogue_asset_keys": ["dak_target"],
            "dialogue_ids": ["scene_001_dialogue_004"],
        }
        self.assertEqual(VPE.segment_asset_key(segment), "dak_target")
        self.assertEqual(IPG.first_dialogue_asset_key(segment), "dak_target")
        self.assertEqual(VOPG.first_dialogue_asset_key(segment), "dak_target")

    def test_frontend_new_dialogue_generates_independent_edit_and_asset_keys(self) -> None:
        source = FRONTEND_MODEL.read_text(encoding="utf-8")
        self.assertNotIn("manualDialogueAssetKey", source)
        self.assertNotIn("newManualDialogueFields", source)
        self.assertIn('dialogue_id: uniqueId("dlg"', source)
        self.assertIn('dialogue_asset_key: uniqueId("dak"', source)
        self.assertNotIn('dialogue.dialogue_id = `${scene.scene_id}_dialogue_', source)


if __name__ == "__main__":
    unittest.main()
