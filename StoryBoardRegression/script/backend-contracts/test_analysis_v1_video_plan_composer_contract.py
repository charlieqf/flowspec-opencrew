from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSER_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "06_01_VideoPlanComposer.py"


def load_composer_module():
    spec = importlib.util.spec_from_file_location("analysis_v1_video_plan_composer", COMPOSER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Cannot load composer module from {COMPOSER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1VideoPlanComposerContractTest(unittest.TestCase):
    def _make_synthetic_clip(
        self,
        composer,
        path: Path,
        *,
        size: str,
        fps: int,
        duration: float,
        color: str,
        frequency: int,
        audio_first: bool,
    ) -> None:
        command = [
            composer.ffmpeg_executable(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:r={fps}:d={duration:.3f}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:d={duration:.3f}",
        ]
        if audio_first:
            command.extend(["-map", "1:a:0", "-map", "0:v:0"])
        else:
            command.extend(["-map", "0:v:0", "-map", "1:a:0"])
        command.extend([
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-shortest",
            str(path),
        ])
        subprocess.run(command, capture_output=True, text=True, check=True)

    def test_compose_videos_keeps_heterogeneous_segments(self) -> None:
        composer = load_composer_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            first = workspace / "first.mp4"
            second = workspace / "second.mp4"
            output = workspace / "output.mp4"
            self._make_synthetic_clip(composer, first, size="720x1280", fps=24, duration=0.8, color="red", frequency=440, audio_first=True)
            self._make_synthetic_clip(composer, second, size="360x640", fps=25, duration=0.7, color="blue", frequency=880, audio_first=False)

            result = composer.compose_videos(workspace, [first, second], output, "heterogeneous_test")
            metadata = composer.probe_media(output)

        self.assertEqual(result["source"], "ffmpeg_concat_filter_reencode")
        self.assertEqual(result["input_count"], 2)
        self.assertGreaterEqual(metadata["duration_seconds"], 1.4)
        self.assertGreaterEqual(result["duration_seconds"], 1.4)

    def test_hyperframe_subtitle_failure_falls_back_to_plain_scene_video(self) -> None:
        composer = load_composer_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            segment_rel = "SessionOutput/storyboard/Working/segment_001_Final.mp4"
            segment_path = workspace / segment_rel
            segment_path.parent.mkdir(parents=True)
            segment_path.write_bytes(b"segment")

            args = composer.Args(
                workspace=str(workspace),
                target_type="scene",
                shot_id="shot_001",
                scene_id="scene_001",
                subtitle_mode="hyperframe",
                watermark_mode="never",
                force=False,
                resume=False,
            )
            shot = {"shot_id": "shot_001"}
            scene = {
                "scene_id": "scene_001",
                "segments": [
                    {
                        "segment_id": "segment_001",
                        "planned_outputs": {"video_path": segment_rel},
                    }
                ],
            }
            result = {"warnings": [], "created_files": [], "backups": []}

            def fake_compose_videos(_workspace, _input_paths, output_path, _label):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"scene")
                return {"status": "completed"}

            with (
                mock.patch.object(composer, "probe_media", return_value={"duration_seconds": 1.0}),
                mock.patch.object(composer, "process_watermark", side_effect=lambda _workspace, video_path, *_args: (video_path, {"status": "skipped"})),
                mock.patch.object(composer, "compose_videos", side_effect=fake_compose_videos),
                mock.patch.object(composer, "subtitles_for_scene", return_value=[{"start": 0, "end": 1, "text": "hello"}]),
                mock.patch.object(composer, "write_subtitle_files", return_value=(workspace / "subtitles.srt", workspace / "subtitles.json")),
                mock.patch.object(composer, "publish_file", side_effect=lambda _workspace, _source, planned_rel, _result: planned_rel),
                mock.patch.object(composer, "publish_json", side_effect=lambda _workspace, _payload, planned_rel, _result: planned_rel),
                mock.patch.object(composer, "render_hyperframe_subtitles", side_effect=composer.ToolError("subtitle timeout")),
            ):
                scene_result = composer.compose_scene(workspace, args, {}, shot, scene, result)

        self.assertEqual(scene_result["status"], "completed")
        self.assertTrue(scene_result["outputs"]["scene_video_path"].endswith("_Scene_Final.mp4"))
        self.assertEqual(scene_result["outputs"]["scene_subtitled_video_path"], "")
        self.assertEqual(scene_result["hyperframe"]["status"], "skipped")
        self.assertEqual(scene_result["hyperframe"]["reason"], "hyperframe_subtitle_render_failed")
        self.assertEqual(result["warnings"][0]["code"], "hyperframe_subtitle_render_failed")


if __name__ == "__main__":
    unittest.main()
