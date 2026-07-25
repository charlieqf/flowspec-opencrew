from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
EXECUTORS = (
    REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
    REPO_ROOT / "ToolLibrary" / "TalkingHead_V1" / "05_02_VideoPlanExecutor.py",
)


class AudioSynthesisQualityContractTest(unittest.TestCase):
    def test_all_0502_executors_copy_aligned_video_and_use_high_quality_retime(self) -> None:
        for executor in EXECUTORS:
            with self.subTest(executor=executor):
                source = executor.read_text(encoding="utf-8")
                self.assertIn('FFMPEG_HIGH_QUALITY_VIDEO_ARGS = ("-crf", "10", "-tune", "film")', source)
                self.assertIn('FFMPEG_HIGH_QUALITY_VIDEO_PRESET = "slow"', source)
                self.assertIn('"source": "ffmpeg_audio_replace_stream_copy"', source)
                self.assertIn('"source": "ffmpeg_audio_replace_target_duration_stream_copy"', source)
                self.assertIn('"video_reencoded": True', source)
                self.assertIn('"quality_mode": "high_quality_crf10"', source)
                self.assertIn('"quality_mode": "stream_copy" if video_copy else "high_quality_crf10"', source)
                self.assertIn("def media_pixel_format(path: Path) -> str:", source)
                self.assertIn('"pixel_format": pixel_format', source)


if __name__ == "__main__":
    unittest.main()
