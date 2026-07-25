from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
TOOLLIB_PATH = REPO_ROOT / "ToolLibrary"
for path in (BACKEND_PATH, TOOLLIB_PATH):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from opcrew_backend.media_library_analysis.visual import load_visual_result, summarize_visual_output  # noqa: E402


def load_tool(filename: str, module_name: str):
    path = REPO_ROOT / "ToolLibrary" / "OpenCut_V1" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OpenCutV1VisualContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.detect = load_tool("03_01_VideoSceneDetect.py", "open_cut_v1_scene_detect")
        cls.index = load_tool("03_02_SceneKeyframeIndex.py", "open_cut_v1_scene_keyframe")

    def test_registry_contains_independent_scene_detect_tools(self) -> None:
        root = REPO_ROOT / "ToolLibrary" / "OpenCut_V1"
        registry = json.loads((root / "tool_registry.json").read_text(encoding="utf-8"))
        tools = {tool["id"]: tool for tool in registry["tools"]}
        self.assertEqual(tools["03_01"]["script"], "ToolLibrary/OpenCut_V1/03_01_VideoSceneDetect.py")
        self.assertEqual(tools["03_02"]["script"], "ToolLibrary/OpenCut_V1/03_02_SceneKeyframeIndex.py")
        self.assertFalse(tools["03_01"]["uses_llm"])
        self.assertFalse(tools["03_01"]["uses_vlm"])
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("ToolLibrary.Analysis_V1", source, path)
            self.assertNotIn("ToolLibrary.Analysis.", source, path)

    def test_scene_timeline_covers_video_without_gaps(self) -> None:
        cuts = [
            {"time": 2.0, "frame": 50, "source_detectors": ["content", "adaptive"], "confidence": 0.82},
            {"time": 4.5, "frame": 112, "source_detectors": ["content"], "confidence": 0.7},
        ]
        scenes = self.detect.build_scenes(cuts, duration=6.0, fps=25.0, frame_count=150)
        self.assertEqual([scene["scene_id"] for scene in scenes], ["scene_0001", "scene_0002", "scene_0003"])
        self.assertEqual(scenes[0]["start"], 0.0)
        self.assertEqual(scenes[-1]["end"], 6.0)
        self.assertTrue(all(left["end"] == right["start"] for left, right in zip(scenes, scenes[1:])))

    def test_no_cut_video_is_one_scene(self) -> None:
        scenes = self.detect.build_scenes([], duration=3.0, fps=24.0, frame_count=72)
        self.assertEqual(len(scenes), 1)
        self.assertEqual((scenes[0]["start"], scenes[0]["end"]), (0.0, 3.0))

    def test_long_fixed_camera_video_is_split_into_bounded_analysis_windows(self) -> None:
        scenes = self.detect.build_scenes([], duration=74.0, fps=25.0, frame_count=1850)
        self.assertEqual(len(scenes), 5)
        self.assertEqual(scenes[0]["start"], 0.0)
        self.assertEqual(scenes[-1]["end"], 74.0)
        self.assertTrue(all(scene["segment_kind"] == "long_scene_window" for scene in scenes))
        self.assertTrue(all(float(scene["duration"]) <= 15.0 for scene in scenes))
        self.assertTrue(all(left["end"] == right["start"] for left, right in zip(scenes, scenes[1:])))
        self.assertEqual([scene["scene_id"] for scene in scenes], [f"scene_{index:04d}" for index in range(1, 6)])

    def test_long_scene_windows_preserve_detected_cut_boundaries(self) -> None:
        scenes = self.detect.build_scenes(
            [{"time": 20.0, "frame": 500, "source_detectors": ["content"], "confidence": 0.7}],
            duration=35.0,
            fps=25.0,
            frame_count=875,
        )
        self.assertEqual([(scene["start"], scene["end"]) for scene in scenes], [(0.0, 10.0), (10.0, 20.0), (20.0, 35.0)])
        self.assertEqual([scene["source_scene_index"] for scene in scenes], [1, 1, 2])

    def test_keyframe_index_has_four_ordered_samples_per_scene(self) -> None:
        scenes_payload = {"scenes": self.detect.build_scenes(
            [{"time": 2.0, "frame": 50, "source_detectors": ["content"], "confidence": 0.7}],
            duration=4.0, fps=25.0, frame_count=100,
        )}
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source.mp4"
            source.write_bytes(b"video")

            def fake_extract(_source: Path, output: Path, times: list[float]) -> float:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"jpeg")
                return times[0]

            with patch.object(self.index, "extract_frame", side_effect=fake_extract):
                payload = self.index.build_final_items(scenes_payload, source=source, workspace=workspace, video_duration=4.0)

            self.assertEqual(payload["scene_count"], 2)
            self.assertEqual(len(payload["items"]), 2)
            frames = [
                frame
                for item in payload["items"]
                for frame in item["keyframes"]
            ]
            self.assertEqual(len(frames), 8)
            self.assertEqual(len({item["image_path"] for item in frames}), 8)
            self.assertTrue(
                all((workspace / item["image_path"]).is_file() for item in frames)
            )
            self.assertEqual(
                [frame["keyframe_time"] for frame in payload["items"][0]["keyframes"]],
                [0.25, 0.75, 1.25, 1.75],
            )
            self.assertEqual(payload["items"][0]["keyframe_time"], 0.75)
            self.assertEqual(payload["sampling_strategy"], "scene_uniform_4_v1")

    def test_four_sampling_slots_have_fixed_targets_and_local_retries(self) -> None:
        expected = (2.5, 7.5, 12.5, 17.5)
        for slot_index, target in enumerate(expected):
            times = self.index.candidate_times(
                0.0,
                20.0,
                20.0,
                slot_index=slot_index,
            )
            self.assertEqual(times[0], target)
            self.assertTrue(
                all(
                    slot_index * 5.0 <= value < (slot_index + 1) * 5.0
                    for value in times
                )
            )

    def test_one_failed_sampling_slot_fails_the_whole_structure_build(self) -> None:
        scenes_payload = {
            "scenes": self.detect.build_scenes(
                [], duration=4.0, fps=25.0, frame_count=100
            )
        }
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source.mp4"
            source.write_bytes(b"video")

            def fail_third_slot(
                _source: Path, output: Path, times: list[float]
            ) -> float:
                if output.name.endswith("sample-03.jpg"):
                    raise RuntimeError("slot exhausted")
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"jpeg")
                return times[0]

            with (
                patch.object(
                    self.index,
                    "extract_frame",
                    side_effect=fail_third_slot,
                ),
                self.assertRaisesRegex(RuntimeError, "slot exhausted"),
            ):
                self.index.build_final_items(
                    scenes_payload,
                    source=source,
                    workspace=workspace,
                    video_duration=4.0,
                )
            self.assertFalse(
                (workspace / "SessionOutput/visual/final_scene_frame_items.json").exists()
            )

    def test_resume_rejects_changed_or_reordered_four_frame_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            frame_root = workspace / "SessionOutput/visual/scene_frames"
            frame_root.mkdir(parents=True)
            keyframes = []
            for index in range(1, 5):
                frame = frame_root / f"scene_0001-sample-{index:02d}.jpg"
                frame.write_bytes(f"frame-{index}".encode())
                keyframes.append(
                    {
                        "keyframe_id": f"scene_0001-sample-{index:02d}",
                        "image_path": frame.relative_to(workspace).as_posix(),
                        "image_sha256": hashlib.sha256(
                            frame.read_bytes()
                        ).hexdigest(),
                    }
                )
            final_path = workspace / self.index.SESSION_FINAL_REL
            final_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "source_fingerprint": "fingerprint",
                "sampling_strategy": "scene_uniform_4_v1",
                "items": [{"scene_id": "scene_0001", "keyframes": keyframes}],
            }
            final_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIsNotNone(
                self.index.reusable(workspace, "fingerprint")
            )
            keyframes[0]["image_sha256"] = "0" * 64
            final_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIsNone(self.index.reusable(workspace, "fingerprint"))

    def test_backend_maps_visual_output_to_scene_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            tool_id = "tus_visual_test"
            root = workspace / "tool_use_sessions" / tool_id
            visual = root / "SessionOutput" / "visual"
            frames = visual / "scene_frames"
            frames.mkdir(parents=True)
            for index in range(1, 5):
                (frames / f"scene_0001-sample-{index:02d}.jpg").write_bytes(b"jpeg")
            (visual / "final_scene_frame_items.json").write_text(json.dumps({
                "sampling_strategy": "scene_uniform_4_v1",
                "items": [{
                    "scene_id": "scene_0001",
                    "title": "Scene 0001",
                    "start": 0.0,
                    "end": 2.0,
                    "duration": 2.0,
                    "keyframe_time": 0.75,
                    "image_path": "SessionOutput/visual/scene_frames/scene_0001-sample-02.jpg",
                    "sampling_strategy": "scene_uniform_4_v1",
                    "keyframes": [
                        {
                            "keyframe_id": f"scene_0001-sample-{index:02d}",
                            "keyframe_time": time_value,
                            "image_path": (
                                "SessionOutput/visual/scene_frames/"
                                f"scene_0001-sample-{index:02d}.jpg"
                            ),
                        }
                        for index, time_value in enumerate(
                            (0.25, 0.75, 1.25, 1.75), start=1
                        )
                    ],
                    "usability": "detected",
                }]
            }), encoding="utf-8")

            result = load_visual_result(workspace=workspace, session_id=53, tool_use_session_id=tool_id, preview_url="/video")
            self.assertIsNone(result["error"])
            self.assertEqual(result["items"][0]["fragment_id"], "scene_0001")
            self.assertEqual(result["items"][0]["title"], "画面片段 1")
            self.assertEqual(result["items"][0]["summary"], "场景切分片段")
            self.assertEqual(result["items"][0]["preview_url"], "/video")
            self.assertEqual(len(result["items"][0]["keyframes"]), 4)
            self.assertEqual(result["items"][0]["keyframes"][1]["time"], 0.75)
            self.assertEqual(
                result["items"][0]["representative_keyframe_id"],
                "scene_0001-sample-02",
            )
            self.assertIn(
                f"tool_use_sessions/{tool_id}/SessionOutput/visual/scene_frames/scene_0001-sample-02.jpg",
                result["items"][0]["keyframes"][1]["image_url"],
            )
            self.assertEqual(summarize_visual_output(root)["fragment_count"], 1)


if __name__ == "__main__":
    unittest.main()
