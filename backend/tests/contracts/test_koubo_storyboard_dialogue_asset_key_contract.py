from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1"
BACKEND_PATH = REPO_ROOT / "backend"
FRONTEND_MODEL = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardModel.js"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.koubo.koubo_storyboard.asset_core_services import (  # noqa: E402
    derive_dialogue_asset_key,
)


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

    def test_executor_indexes_by_asset_key_and_preserves_legacy_keys(self) -> None:
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
        # Legacy srt_id/dialogue_id keys are intentionally preserved in the index for
        # backward compatibility (commit c813392 "preserve legacy dialogue audio keys").
        # Consequence: binding resolves the segment key as dialogue_asset_keys, falling
        # back to dialogue_ids, so a segment carrying only a legacy dialogue_id still binds.
        self.assertIn("dak_target", index)
        self.assertIn("srt_0004", index)
        self.assertIn("scene_001_dialogue_004", index)

        # Preferred path: bind via dialogue_asset_key.
        self.assertTrue(VPE.bind_segment_output_to_storyboard(
            {"dialogue_asset_keys": ["dak_target"], "dialogue_ids": ["scene_001_dialogue_004"]},
            index,
            "video",
            "SessionOutput/storyboard/Working/dak_target_Video_Final.mp4",
        ))
        dialogue = storyboard["shots"][0]["scenes"][0]["dialogue_items"][0]
        self.assertEqual(dialogue["working_assets"]["video"]["path"], "SessionOutput/storyboard/Working/dak_target_Video_Final.mp4")

        # Legacy path: a segment with only a legacy dialogue_id still resolves via the
        # preserved index entry and binds (backward-compat behavior).
        self.assertTrue(VPE.bind_segment_output_to_storyboard(
            {"dialogue_ids": ["scene_001_dialogue_004"]},
            index,
            "video",
            "SessionOutput/storyboard/Working/dak_target_Video_Final.mp4",
        ))

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

    def test_historical_source_initializes_stable_key_from_srt_id(
        self,
    ) -> None:
        sc = SimpleNamespace(
            text=lambda value: str(value or "").strip()
        )
        dialogue = {"srt_id": "srt_0004"}
        self.assertEqual(
            derive_dialogue_asset_key(dialogue, sc=sc),
            "srt_0004",
        )
        self.assertNotEqual(
            derive_dialogue_asset_key(
                dialogue, {"srt_0004"}, sc=sc
            ),
            "srt_0004",
        )
        self.assertNotEqual(
            derive_dialogue_asset_key(
                {"srt_id": "../unsafe"}, sc=sc
            ),
            "../unsafe",
        )


if __name__ == "__main__":
    unittest.main()
