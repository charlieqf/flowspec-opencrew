from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
OPENCLIP_BACKEND_ROOT = REPO_ROOT / "backend"
NORMALIZE_SCRIPT_PATH = BACKEND_ROOT / "scripts" / "normalize_koubo_storyboard_simplified_chinese.py"
for path in (BACKEND_ROOT, OPENCLIP_BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def load_normalize_script():
    spec = importlib.util.spec_from_file_location("normalize_koubo_storyboard_contract", NORMALIZE_SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class KouboStoryboardSimplifiedChineseContractTest(unittest.TestCase):
    def test_recalculate_normalizes_visible_storyboard_text_without_touching_paths(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard.asset_core_services import register_asset_core_services
        from opcrew_backend.koubo.koubo_storyboard.storyboard_plan_services import register_storyboard_plan_services
        from opcrew_backend.koubo.koubo_storyboard.value_services import register_value_services

        ns = SimpleNamespace()
        register_value_services(ns)
        register_asset_core_services(ns)
        register_storyboard_plan_services(ns)

        plan = {
            "schema_version": "koubo_storyboard_edit_0.1",
            "video_formula": "傳統貨架口播",
            "shots": [{
                "shot_name": "零食批發",
                "formula_stage": "換到門店貨架",
                "summary": "兩塊錢的爆款小貨",
                "scenes": [{
                    "scene_name": "門店貨架上去賣",
                    "summary": "你覺得這土豪難做嗎？",
                    "working_assets": {},
                    "dialogues": [{
                        "srt_id": "srt_001",
                        "text": "一包就能賣到十幾二十塊",
                        "duration": 1.2,
                        "image_path": "SessionOutput/visual/貨架/frame_001.jpg",
                        "source_image_paths": ["SessionOutput/visual/貨架/frame_001.jpg"],
                        "working_assets": {},
                    }],
                }],
            }],
        }

        result = ns.recalculate(plan, sc=ns)
        self.assertEqual(result["video_formula"], "传统货架口播")
        shot = result["shots"][0]
        scene = shot["scenes"][0]
        dialogue = scene["dialogues"][0]

        self.assertEqual(shot["shot_name"], "零食批发")
        self.assertEqual(shot["formula_stage"], "换到门店货架")
        self.assertEqual(shot["summary"], "两块钱的爆款小货")
        self.assertEqual(scene["scene_name"], "门店货架上去卖")
        self.assertEqual(scene["summary"], "你觉得这土豪难做吗？")
        self.assertEqual(dialogue["text"], "一包就能卖到十几二十块")
        self.assertEqual(dialogue["image_path"], "SessionOutput/visual/貨架/frame_001.jpg")
        self.assertEqual(dialogue["source_image_paths"], ["SessionOutput/visual/貨架/frame_001.jpg"])

    def test_normalize_script_converts_whitelisted_source_fields_only(self) -> None:
        module = load_normalize_script()
        payload = {
            "video_formula": "傳統貨架口播",
            "shots": [{
                "title": "零食批發",
                "formula_stage": "換到門店貨架",
                "key_frame_paths": ["SessionOutput/visual/貨架/frame_001.jpg"],
                "scenes": [{
                    "title": "門店貨架上去賣",
                    "key_frame_paths": ["SessionOutput/visual/貨架/frame_001.jpg"],
                    "dialogue_items": [{
                        "dialogue": "你覺得這土豪難做嗎？",
                        "image_path": "SessionOutput/visual/貨架/frame_001.jpg",
                    }],
                }],
            }],
        }

        changes = module.normalize_storyboard_payload(payload, "source")

        self.assertGreaterEqual(len(changes), 4)
        self.assertEqual(payload["video_formula"], "传统货架口播")
        self.assertEqual(payload["shots"][0]["title"], "零食批发")
        self.assertEqual(payload["shots"][0]["formula_stage"], "换到门店货架")
        self.assertEqual(payload["shots"][0]["scenes"][0]["title"], "门店货架上去卖")
        self.assertEqual(payload["shots"][0]["scenes"][0]["dialogue_items"][0]["dialogue"], "你觉得这土豪难做吗？")
        self.assertEqual(payload["shots"][0]["key_frame_paths"], ["SessionOutput/visual/貨架/frame_001.jpg"])
        self.assertEqual(payload["shots"][0]["scenes"][0]["dialogue_items"][0]["image_path"], "SessionOutput/visual/貨架/frame_001.jpg")


if __name__ == "__main__":
    unittest.main()
