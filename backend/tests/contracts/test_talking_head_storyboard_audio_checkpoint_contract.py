from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "ToolLibrary" / "TalkingHead_V1" / "03_StoryBoardConfig.py"


def load_module():
    spec = importlib.util.spec_from_file_location("talking_head_storyboard_config_checkpoint_contract", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TalkingHeadStoryboardAudioCheckpointContractTest(unittest.TestCase):
    def test_successful_audio_is_bound_before_a_later_tts_failure(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            variables = {
                "workflow_id": "person_talking_head_v1",
                "task": {"task_id": 45, "session_id": 46},
                "talking_head": {
                    "voice_timing": {"provider": "heygen", "voice_id": "voice-1", "voice_label": "Test", "tempo": 1.5},
                    "segment_planning": {"srt_target_seconds": 15, "portrait_segments_per_image": 2},
                },
            }
            dialogues = []
            for index in range(1, 4):
                dialogues.append({
                    "dialogue_id": f"scene_001_dialogue_{index:03d}",
                    "dialogue_asset_key": f"dialogue_{index:03d}",
                    "srt_id": f"srt_{index:04d}",
                    "srt_ids": [f"srt_{index:04d}"],
                    "dialogue": f"测试对白{index}",
                    "start": (index - 1) * 15,
                    "end": index * 15,
                    "duration": 15,
                    "source_srt_items": [{"srt_id": f"srt_{index:04d}", "dialogue": f"测试对白{index}", "estimated_duration": 15}],
                })
            storyboard = {
                "schema_version": "test",
                "shots": [{"scenes": [{"dialogue_items": dialogues}]}],
            }
            module.generate.write_json(workspace / module.generate.VARIABLES_REL, variables)
            module.generate.write_json(workspace / module.generate.STORYBOARD_REL, storyboard)
            subtitle_dir = workspace / "SessionOutput" / "subtitle"
            subtitle_dir.mkdir(parents=True)
            subtitle_payload = {"items": [{"srt_id": f"srt_{index:04d}", "dialogue": f"测试对白{index}"} for index in range(1, 4)]}
            (subtitle_dir / "final_srt_frame_items.json").write_text(json.dumps(subtitle_payload, ensure_ascii=False), encoding="utf-8")
            (subtitle_dir / "rewritten_srt_items.json").write_text(json.dumps(subtitle_payload, ensure_ascii=False), encoding="utf-8")

            calls = 0

            def fake_tts(provider, text, voice_id, tempo, output_path, **kwargs):
                nonlocal calls
                self.assertEqual(provider, "heygen")
                calls += 1
                if calls == 3:
                    raise TimeoutError("TTS timeout")
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"audio")
                return {"duration_seconds": float(10 + calls), "cache_hit": False}

            with patch.object(module, "generate_clone_audio", side_effect=fake_tts):
                with self.assertRaisesRegex(TimeoutError, "TTS timeout"):
                    module.run(workspace)

            persisted = json.loads((workspace / module.generate.STORYBOARD_REL).read_text(encoding="utf-8"))
            persisted_dialogues = persisted["shots"][0]["scenes"][0]["dialogue_items"]
            self.assertEqual(persisted["talking_head_configuration_status"], "partial")
            self.assertEqual(persisted["talking_head_configured_dialogue_count"], 2)
            self.assertEqual(persisted_dialogues[0]["working_assets"]["audio"]["path"], "SessionOutput/storyboard/Working/dialogue_001_Audio_Final.wav")
            self.assertEqual(persisted_dialogues[1]["working_assets"]["audio"]["path"], "SessionOutput/storyboard/Working/dialogue_002_Audio_Final.wav")
            self.assertFalse(persisted_dialogues[2].get("working_assets"))
            report = json.loads((workspace / module.REPORT_REL).read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["outputs"]["configured_dialogue_count"], 2)


if __name__ == "__main__":
    unittest.main()
