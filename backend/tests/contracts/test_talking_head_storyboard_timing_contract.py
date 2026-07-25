from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "ToolLibrary" / "TalkingHead_V1" / "02_StoryBoardStructure.py"


def load_module():
    spec = importlib.util.spec_from_file_location("talking_head_storyboard_structure_contract", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TalkingHeadStoryboardTimingContractTest(unittest.TestCase):
    def test_calibrated_timing_is_allocated_grouped_and_persisted_per_line(self) -> None:
        module = load_module()
        source_items = [
            {"index": 1, "srt_id": "srt_0001", "dialogue": "一二三四", "start": 0, "end": 15, "duration": 15},
            {"index": 2, "srt_id": "srt_0002", "dialogue": "五六七八九十", "start": 15, "end": 30, "duration": 15},
        ]
        calibration = {
            "status": "completed",
            "seconds_per_unit": 0.25,
            "duration_seconds": 2.5,
            "units": 10,
            "audio_path": "SessionOutput/storyboard/Working/talking_head_voice_calibration.wav",
        }
        timed = module.retime_items(source_items, 0.25, "heygen_voice_calibration", calibration)
        self.assertEqual([(item["start"], item["end"], item["duration"]) for item in timed], [(0.0, 1.0, 1.0), (1.0, 2.5, 1.5)])

        grouped, warnings = module.group_items_to_dialogues(timed, 0.25, 2.0, "heygen_voice_calibration", calibration)
        self.assertEqual(warnings, [])
        self.assertEqual(len(grouped), 2)
        self.assertEqual(grouped[0]["source_srt_items"][0]["end"], 1.0)
        self.assertEqual(grouped[1]["start"], 1.0)

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            subtitle_dir = workspace / "SessionOutput" / "subtitle"
            subtitle_dir.mkdir(parents=True)
            final_payload = {"schema_version": "test", "items": [{**item, "dialogue": f"参考{index}"} for index, item in enumerate(source_items, 1)]}
            rewritten_payload = {"schema_version": "test", "items": source_items}
            (subtitle_dir / "final_srt_frame_items.json").write_text(json.dumps(final_payload, ensure_ascii=False), encoding="utf-8")
            (subtitle_dir / "rewritten_srt_items.json").write_text(json.dumps(rewritten_payload, ensure_ascii=False), encoding="utf-8")

            written = module.persist_retimed_subtitles(workspace, timed, calibration)
            self.assertEqual(len(written), 3)
            persisted_final = json.loads((subtitle_dir / "final_srt_frame_items.json").read_text(encoding="utf-8"))
            persisted_rewritten = json.loads((subtitle_dir / "rewritten_srt_items.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted_final["items"][0]["dialogue"], "参考1")
            self.assertEqual(persisted_final["items"][1]["end"], 2.5)
            self.assertEqual(persisted_rewritten["items"][0]["duration"], 1.0)
            self.assertEqual(persisted_rewritten["timing_source"], "heygen_voice_calibration")
            rendered_srt = (subtitle_dir / "rewritten_dialogue.srt").read_text(encoding="utf-8")
            self.assertIn("00:00:01,000 --> 00:00:02,500", rendered_srt)


if __name__ == "__main__":
    unittest.main()
